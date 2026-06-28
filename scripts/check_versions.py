#!/usr/bin/env python3
"""Check pinned container versions against registries and update resources.toml."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESOURCES_TOML = REPO / "komodo-resources" / "resources.toml"
GHCR_TOKEN = None
GITHUB_TOKEN = None


def log(msg):
    print(f"  {msg}")


# ── Registry API calls ─────────────────────────────────────

def registry_tags(image):
    """Return list of tag strings for an image from its registry, or empty list."""
    parts = image.split("/")
    registry = "docker.io"
    rest = parts
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0]):
        registry = parts[0]
        rest = parts[1:]

    ns_repo = "/".join(rest).replace(":", "/")

    try:
        if "ghcr.io" in registry:
            # Exchange PAT for a GHCR-scoped bearer token
            token_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{ns_repo}:pull"
            token_req = urllib.request.Request(token_url)
            if GITHUB_TOKEN:
                token_req.add_header("Authorization", f"Basic {base64.b64encode(f'unused:{GITHUB_TOKEN}'.encode()).decode()}")
            with urllib.request.urlopen(token_req, timeout=15) as resp:
                bearer = json.loads(resp.read())["token"]

            url = f"https://ghcr.io/v2/{ns_repo}/tags/list"
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {bearer}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                return data.get("tags", [])
        elif "quay.io" in registry:
            url = f"https://quay.io/api/v1/repository/{ns_repo}/tag/"
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
                return [t["name"] for t in data.get("tags", [])]
        else:
            ns, repo = ns_repo.split("/", 1) if "/" in ns_repo else ("library", ns_repo)
            url = f"https://hub.docker.com/v2/namespaces/{ns}/repositories/{repo}/tags?page_size=100"
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())
                return [t["name"] for t in data.get("results", [])]
    except Exception as e:
        log(f"  ⚠ registry error for {image}: {e}")
        return []


# ── Version comparison ─────────────────────────────────────

def version_key(value):
    """Extract a comparable version key from a tag string."""
    value = value.strip().strip('"')
    m = re.match(r"^ee-(\d+(?:\.\d+)*)", value)
    if m:
        parts = [int(x) for x in m.group(1).split(".")]
        return tuple(parts)
    m = re.match(r"^v?(\d+(?:\.\d+)*)", value)
    if m:
        parts = [int(x) for x in m.group(1).split(".")]
        return tuple(parts)
    if re.match(r"^\d+(\.\d+)*$", value):
        return tuple(int(x) for x in value.split("."))
    return None


def num_components(value):
    """Count the number of numeric segments in a version value."""
    m = re.match(r"^(?:ee-|v?)(\d+(?:\.\d+)*)", value)
    if m:
        return len(m.group(1).split("."))
    if re.match(r"^\d+(\.\d+)*$", value):
        return len(value.split("."))
    return None


def filter_pattern(value):
    """Build a regex pattern to filter relevant tags from the registry."""
    value = value.strip().strip('"')
    if value == "latest" or "latest" in value:
        return None
    m = re.match(r"^(v?\d+(?:\.\d+)*)", value)
    if m:
        prefix = value[:m.start(1)]
        suffix = value[m.end(1):]
        prefix_re = re.escape(prefix)
        suffix_re = re.escape(suffix)
        return re.compile(f"^{prefix_re}\\d+(?:\\.\\d+)*{suffix_re}$")
    return re.compile(f"^{re.escape(value)}$")


def find_newer(value, tags):
    """Return the newest tag > value, or None."""
    vk = version_key(value)
    if vk is None:
        return None
    pat = filter_pattern(value)
    if pat is None:
        return None
    cur_components = num_components(value)
    candidates = []
    for t in tags:
        if pat.match(t):
            tk = version_key(t)
            if not tk or tk <= vk:
                continue
            tc = num_components(t)
            if cur_components is not None and tc is not None and tc < cur_components:
                continue
            if cur_components == 1 and tc != 1:
                continue
            # For single-component versions, reject absurd jumps (build hashes, timestamps)
            if cur_components == 1 and tc == 1 and tk[0] > vk[0] * 50:
                continue
            candidates.append((tk, t, tc))
    if not candidates:
        return None
    # Prefer candidates with same component count as current value
    same_count = [(tk, t) for tk, t, tc in candidates if tc == cur_components]
    if same_count:
        same_count.sort(key=lambda x: x[0], reverse=True)
        return same_count[0][1]
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ── Release URL helpers ────────────────────────────────────

def release_url(image_repo, tag):
    """Derive a GitHub release URL from an image repo and tag, or None."""
    m = re.match(r"^ghcr\.io/([^/]+/[^/]+?)(?::[^:]*)?$", image_repo)
    if m:
        return f"https://github.com/{m.group(1)}/releases/tag/{tag}"
    m = re.match(r"^quay\.io/([^/]+/[^/]+?)(?::[^:]*)?$", image_repo)
    if m:
        return f"https://github.com/{m.group(1)}/releases/tag/{tag}"
    m = re.match(r"^(?:docker\.io/)?([^/]+/[^/]+?)(?::[^:]*)?$", image_repo)
    if m:
        ns, repo_name = m.group(1).split("/", 1)
        if ns == "library":
            return f"https://github.com/docker-library/{repo_name}/releases/tag/{tag}"
        return f"https://github.com/{m.group(1)}/releases/tag/{tag}"
    # Bare image name like "postgres" or "mongo" — docker official library
    if "/" not in image_repo and ":" not in image_repo:
        return f"https://github.com/docker-library/{image_repo}/releases/tag/{tag}"
    return None


# ── Image scanning ─────────────────────────────────────────

COMPOSE_IMAGE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
IMAGE_VAR_IN_LINE = re.compile(r"^\s*image:\s*(\S+?)(?::\$\{([A-Za-z_][A-Za-z0-9_]*)\})?\s*$")


def scan_stack_images(run_dir):
    """Yield (variable_name, image_repo) for each image: line in compose files."""
    d = REPO / run_dir
    if not d.exists():
        return
    files = sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml"))
    for f in files:
        for line in f.read_text().splitlines():
            m = re.match(r"^\s*image:\s*(.+?)(?::\$\{([A-Za-z_][A-Za-z0-9_]*)\})?\s*$", line)
            if m:
                repo = m.group(1)
                var = m.group(2)
                if var:
                    yield var, repo


# ── Main ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Check pinned container versions against registries."
    )
    parser.add_argument(
        "--check", "-c",
        action="store_true",
        help="Check only — report available updates without modifying resources.toml",
    )
    args = parser.parse_args()

    global GITHUB_TOKEN
    GITHUB_TOKEN = os.environ.get("GHCR_TOKEN", "")
    if not GITHUB_TOKEN:
        try:
            token = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
            ).stdout.strip()
            if token:
                GITHUB_TOKEN = token
        except Exception:
            pass

    if not RESOURCES_TOML.exists():
        log("✖ resources.toml not found")
        sys.exit(1)

    data = tomllib.loads(RESOURCES_TOML.read_text())
    content = RESOURCES_TOML.read_text()

    # Collect updates: {var_name: [(old, new, stack_name, image_repo), ...]}
    updates = {}

    for s in data.get("stack", []):
        name = s["name"]
        run_dir = s.get("config", {}).get("run_directory", "")
        env_block = s.get("config", {}).get("environment", "")

        if not run_dir or not env_block:
            continue

        env_vars = {}
        for line in env_block.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip().strip('"')

        for var_name, image_repo in scan_stack_images(run_dir):
            if var_name not in env_vars:
                continue
            current_val = env_vars[var_name]
            if "latest" in current_val.lower():
                continue

            log(f"  checking {name}/{var_name} ({image_repo}, current={current_val})")

            tags = registry_tags(image_repo)
            if not tags:
                log(f"    ⚠ no tags returned")
                continue

            newer = find_newer(current_val, tags)
            if newer:
                log(f"    ✓ {current_val} → {newer}")
                updates.setdefault(var_name, []).append((current_val, newer, name, image_repo))
            else:
                log(f"    − no newer version found")

    if not updates:
        log("\n✓ no updates found")
        return

    if args.check:
        total = sum(len(v) for v in updates.values())
        log(f"\n✓ {total} update(s) across {len(updates)} variable(s) available (--check mode)")
        for var, changes in sorted(updates.items()):
            stacks = ", ".join(c[2] for c in changes)
            repos = {c[3] for c in changes}
            url = release_url(next(iter(repos)), changes[0][1]) if repos else None
            line = f"  {var}: {changes[0][0]} → {changes[0][1]} ({stacks})"
            if url:
                line += f"\n    {url}"
            log(line)
        return

    # ── Create PRs per variable via gh ──
    base_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=10
    ).stdout.strip()

    for var_name, changes in sorted(updates.items()):
        old, new, first_stack = changes[0]
        stacks_list = ", ".join(c[2] for c in changes)
        branch = f"update/{var_name}"

        # Check if PR/remote branch already exists
        pr_check = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open",
             "--json", "number", "--jq", "length"],
            capture_output=True, text=True, timeout=15,
        )
        if pr_check.returncode == 0 and pr_check.stdout.strip() not in ("", "0"):
            log(f"  ⏭ PR already exists for {var_name}, skipping")
            continue

        # Read fresh content and apply only this var's changes
        toml_content = RESOURCES_TOML.read_text()
        for c_old, c_new, _ in changes:
            regex = re.compile(
                rf'^(\s*{re.escape(var_name)}\s*=\s*["\']?){re.escape(c_old)}(["\']?\s*)$',
                re.MULTILINE,
            )
            if regex.search(toml_content):
                toml_content = regex.sub(rf"\g<1>{c_new}\g<2>", toml_content)
            else:
                log(f"    ⚠ could not find line for {var_name}={c_old}")

        # Create branch, commit, push, PR
        subprocess.run(["git", "checkout", "-b", branch], check=True, timeout=15)
        RESOURCES_TOML.write_text(toml_content)
        subprocess.run(["git", "add", str(RESOURCES_TOML)], check=True, timeout=15)
        subprocess.run(
            ["git", "commit", "-m", f"chore: update {var_name} to {new}"],
            check=True, timeout=15,
        )
        subprocess.run(
            ["git", "push", "origin", branch],
            check=True, timeout=30,
        )

        repos = {c[3] for c in changes}
        body = f"Updates `{var_name}` from `{old}` to `{new}`.\n\nAffected stacks: {stacks_list}"
        links = []
        for r in sorted(repos):
            url = release_url(r, new)
            if url:
                links.append(f"- [{r}]({url})")
        if links:
            body += "\n\nRelease notes:\n" + "\n".join(links)
        subprocess.run(
            ["gh", "pr", "create",
             "--title", f"chore: update {var_name} to {new}",
             "--body", body,
             "--base", base_branch,
             "--head", branch,
             "--label", "dependencies"],
            check=True, timeout=30,
        )

        # Switch back
        subprocess.run(["git", "checkout", base_branch], check=True, timeout=15)
        log(f"  ✓ PR created: {var_name} → {new}")

    log(f"\n✓ {len(updates)} PR(s) created")


if __name__ == "__main__":
    main()
