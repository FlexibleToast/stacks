#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import tomllib
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOURCES_TOML = REPO_ROOT / "komodo-resources" / "resources.toml"
ENV_TEST = REPO_ROOT / ".env.test"
IGNORE_DIRS = {".git", ".opencode"}
STACK_DIRS = [d for d in REPO_ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")]

errors = []
warnings = []


def err(msg):
    errors.append(msg)
    print(f"  ✖ {msg}")


def warn(msg):
    warnings.append(msg)
    print(f"  ⚠ {msg}")


def ok(msg):
    print(f"  ✓ {msg}")


# ── YAML validation ──────────────────────────────────────────

def validate_yaml():
    print("\n── YAML validation ──")
    files = list(REPO_ROOT.rglob("*.yaml")) + list(REPO_ROOT.rglob("*.yml"))
    files = [f for f in files if f.relative_to(REPO_ROOT).parts[0] not in IGNORE_DIRS]
    count = 0
    for f in sorted(files):
        try:
            yaml.safe_load(f.read_text())
            count += 1
        except yaml.YAMLError as e:
            err(f"{f.relative_to(REPO_ROOT)}: {e}")
    ok(f"{count} files valid")


# ── TOML validation ──────────────────────────────────────────

def validate_toml():
    print("\n── TOML validation ──")
    files = list(REPO_ROOT.rglob("*.toml"))
    files = [f for f in files if f.relative_to(REPO_ROOT).parts[0] not in IGNORE_DIRS]
    count = 0
    for f in sorted(files):
        try:
            tomllib.loads(f.read_text())
            count += 1
        except tomllib.TOMLDecodeError as e:
            err(f"{f.relative_to(REPO_ROOT)}: {e}")
    ok(f"{count} files valid")


# ── Docker Compose validation ────────────────────────────────

def compose_files(d):
    """Return all compose fragment files in a stack directory, with compose.yaml first."""
    files = sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml"))
    files = [f for f in files if f.name != "compose.yaml"]
    return [d / "compose.yaml"] + sorted(files)


def get_stack_envs():
    """Build {run_directory: {var: val}} from resources.toml environment blocks."""
    if not RESOURCES_TOML.exists():
        return {}
    data = tomllib.loads(RESOURCES_TOML.read_text())
    result = {}
    for s in data.get("stack", []):
        run_dir = s.get("config", {}).get("run_directory", "")
        if not run_dir:
            continue
        env_block = s.get("config", {}).get("environment", "")
        vars = {}
        for line in env_block.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vars[k.strip()] = v.strip().strip('"')
        result[run_dir] = vars
    return result


def validate_compose():
    print("\n── Docker Compose validation ──")
    dirs = [d for d in STACK_DIRS if (d / "compose.yaml").exists()]
    stack_envs = get_stack_envs()
    passed = 0
    skipped = 0
    for d in sorted(dirs):
        files = compose_files(d)
        test_env = ENV_TEST if ENV_TEST.exists() else None
        cmd = ["docker", "compose"]
        for f in files:
            cmd.extend(["-f", str(f)])
        cmd.extend(["config", "--quiet"])
        env = os.environ.copy()
        if test_env:
            with open(test_env) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip()
        # Inject env vars from resources.toml (override .env.test)
        if d.name in stack_envs:
            for k, v in stack_envs[d.name].items():
                env[k] = v
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env, check=True)
            ok(f"{d.name}/compose.yaml ({len(files)} file{'s' if len(files) > 1 else ''})")
            passed += 1
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip()
            vars = re.findall(
                r'(?:variable\s+["\']?(\w+)["\']?\s+is\s+not\s+set|refers\s+to\s+an?\s+undefined\s+variable\s+["\']?(\w+)["\']?)',
                stderr, re.IGNORECASE,
            )
            var_names = {v for pair in vars for v in pair if v}
            if var_names:
                err(f"{d.name}: missing env vars: {', '.join(sorted(var_names))}")
            else:
                err(f"{d.name}: {stderr.split(chr(10))[-1]}")
        except FileNotFoundError:
            warn("docker compose not available (skipped)")
            skipped += 1
            break
        except subprocess.TimeoutExpired:
            warn(f"{d.name}/compose.yaml: timed out (skipped)")
            skipped += 1
    if passed or skipped:
        ok(f"{passed} valid, {skipped} skipped")


# ── Cross-reference check ────────────────────────────────────

def get_branch():
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT)
    return r.stdout.strip()


def get_repo_names():
    try:
        data = tomllib.loads(RESOURCES_TOML.read_text())
        return [r["name"] for r in data.get("repo", [])]
    except Exception:
        return []


def parse_stacks(content):
    try:
        data = tomllib.loads(content)
        return data.get("stack", [])
    except Exception:
        return []


def get_base_stacks():
    r = subprocess.run(
        ["git", "show", "origin/main:komodo-resources/resources.toml"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if r.returncode != 0:
        return None
    return parse_stacks(r.stdout)


def validate_cross_reference():
    print("\n── Cross-reference check ──")
    branch = get_branch()
    repo_names = get_repo_names()

    if not RESOURCES_TOML.exists():
        err("resources.toml not found")
        return

    head_stacks = parse_stacks(RESOURCES_TOML.read_text())
    base_stacks = get_base_stacks()

    if base_stacks is None:
        ok(f"origin/main not available, skipping diff check (branch: {branch})")
        return

    head_by_name = {s["name"]: s for s in head_stacks}
    base_by_name = {s["name"]: s for s in base_stacks}

    modified = set()
    for name, s in head_by_name.items():
        head_lr = s.get("config", {}).get("linked_repo", "")
        base_lr = base_by_name.get(name, {}).get("config", {}).get("linked_repo", "") if name in base_by_name else None
        if base_lr is None or head_lr != base_lr:
            modified.add(name)

    if not modified:
        ok("no modified stack entries")
        return

    pr_base = os.environ.get("PR_BASE", "")
    expected_repo = pr_base if pr_base else branch
    expected_repo = expected_repo if expected_repo != "main" else "stacks"
    context = f"merge to {pr_base}" if pr_base else f"on branch \"{branch}\""

    all_ok = True
    for name in sorted(modified):
        s = head_by_name[name]
        linked_repo = s.get("config", {}).get("linked_repo", "")
        if linked_repo != expected_repo:
            err(f"{name}: linked_repo is \"{linked_repo}\", expected \"{expected_repo}\" {context}")
            all_ok = False
    if all_ok:
        ok(f"{len(modified)} modified entries all match expected linked_repo \"{expected_repo}\" {context}")
    else:
        ok(f"valid repos from TOML: {', '.join(repo_names)}")


# ── Main ─────────────────────────────────────────────────────

def main():
    print(f"Branch: {get_branch()}")
    print(f"Repo root: {REPO_ROOT}")

    validate_yaml()
    validate_toml()
    validate_compose()
    validate_cross_reference()

    print(f"\n── Summary ──")
    print(f"  {len(errors)} errors, {len(warnings)} warnings")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
