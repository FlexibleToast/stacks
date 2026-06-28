#!/usr/bin/env python3
"""Detect changed stacks vs previous commit and trigger Komodo DeployStack API."""

import os
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOURCES_TOML = REPO_ROOT / "komodo-resources" / "resources.toml"
API_BASE = os.environ.get("KOMODO_API_BASE", "https://komodo.mcdade.app")
API_KEY = os.environ.get("KOMODO_API_KEY", "")
API_SECRET = os.environ.get("KOMODO_API_SECRET", "")
GIT_BASE = os.environ.get("GIT_BASE", "HEAD~1")


NULL_SHA = "0000000000000000000000000000000000000000"


def get_changed_dirs():
    """Return set of top-level directories changed since GIT_BASE."""
    if not GIT_BASE or GIT_BASE == NULL_SHA:
        print(f"  ⚠ no previous commit to diff against (first push)")
        return None
    r = subprocess.run(
        ["git", "diff", "--name-only", f"{GIT_BASE}...HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if r.returncode != 0:
        print(f"  ⚠ git diff failed: {r.stderr.strip()}")
        return None
    dirs = set()
    for line in r.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = Path(line).parts
        if parts and parts[0] not in (".github", ".opencode"):
            dirs.add(parts[0])
    return dirs


def get_stack_mapping():
    """Build {run_directory: [stack_name, ...]} from resources.toml."""
    if not RESOURCES_TOML.exists():
        return {}
    data = tomllib.loads(RESOURCES_TOML.read_text())
    mapping = defaultdict(list)
    for s in data.get("stack", []):
        run_dir = s.get("config", {}).get("run_directory", "")
        if run_dir:
            mapping[run_dir].append(s["name"])
    return mapping


def _build_toml_sections():
    """Return list of (name, start_line, end_line) for each [[stack]] entry."""
    lines = RESOURCES_TOML.read_text().splitlines()
    sections = []
    current = None
    for i, line in enumerate(lines, 1):
        m = re.match(r"^\[{2}(\w+)\]", line)
        if m:
            if current is not None:
                current["end"] = i - 1
                sections.append(current)
                current = None
            if m.group(1) == "stack":
                current = {"start": i, "name": None}
        elif current is not None:
            m = re.match(r'^name\s*=\s*"(.+)"', line)
            if m:
                current["name"] = m.group(1)
    if current is not None:
        current["end"] = len(lines)
        sections.append(current)
    return [(s["name"], s["start"], s["end"]) for s in sections if s["name"]]


def get_stacks_changed_in_toml(base):
    """Return stack names whose [[stack]] entry changed in resources.toml."""
    if not base or base == NULL_SHA:
        return []
    r = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}...HEAD", "--", str(RESOURCES_TOML)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []

    changed_lines = set()
    for line in r.stdout.splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            if count > 0:
                changed_lines.update(range(start, start + count))

    if not changed_lines:
        return []

    sections = _build_toml_sections()
    affected = set()
    for ln in changed_lines:
        for name, sec_start, sec_end in sections:
            if sec_start <= ln <= sec_end:
                affected.add(name)
                break
    return sorted(affected)


def call_deploy(stack_name):
    """Call Komodo DeployStack API for one stack."""
    r = subprocess.run(
        [
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "-X", "POST",
            f"{API_BASE}/execute/DeployStack",
            "-H", "Content-Type: application/json",
            "-H", f"X-Api-Key: {API_KEY}",
            "-H", f"X-Api-Secret: {API_SECRET}",
            "-d", f'{{"stack": "{stack_name}"}}',
        ],
        capture_output=True, text=True, timeout=60,
    )
    return r.stdout.strip(), r.stderr.strip()


def main():
    if not API_KEY or not API_SECRET:
        print("  ⚠ KOMODO_API_KEY / KOMODO_API_SECRET not set — dry run")
        dry_run = True
    else:
        dry_run = False

    print(f"\n── Changed stacks (since {GIT_BASE}) ──")

    changed = get_changed_dirs()
    if changed is None:
        print("  ⚠ Cannot determine changes")
        sys.exit(0)

    if not changed:
        print("  ✓ no stacks changed")
        return

    mapping = get_stack_mapping()
    if not mapping:
        print("  ⚠ Could not parse resources.toml")
        sys.exit(0)

    triggered = []
    not_found = []
    for d in sorted(changed):
        if d in mapping:
            for stack_name in mapping[d]:
                triggered.append(stack_name)
        elif d == "komodo-resources":
            toml_affected = get_stacks_changed_in_toml(GIT_BASE)
            if toml_affected:
                msg = ", ".join(toml_affected)
                print(f"  → from resources.toml: {msg}")
                for name in toml_affected:
                    if name not in triggered:
                        triggered.append(name)
            else:
                not_found.append(d)
        else:
            not_found.append(d)

    if not_found:
        print(f"  ⚠ no Komodo stack for: {', '.join(not_found)}")

    if not triggered:
        print("  ✓ no deployable stacks changed")
        return

    print(f"  → triggering: {', '.join(triggered)}")

    if dry_run:
        return

    ok = 0
    fail = 0
    for name in triggered:
        code, stderr = call_deploy(name)
        if code.startswith("2"):
            print(f"  ✓ {name} (HTTP {code})")
            ok += 1
        else:
            print(f"  ✖ {name} (HTTP {code}): {stderr[:200]}")
            fail += 1

    if ok:
        print(f"  ✓ {ok} deployed")
    if fail:
        print(f"  ✖ {fail} failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
