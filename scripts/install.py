#!/usr/bin/env python3
"""Install the Codex Agy Consultant skill and optional global guidance."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "codex-agy-consultant"
SKILL_SOURCE = PLUGIN_ROOT / "skills" / "agy-consultant"
GUIDANCE_START = "<!-- codex-agy-consultant:start -->"
GUIDANCE_END = "<!-- codex-agy-consultant:end -->"
GUIDANCE = f"""{GUIDANCE_START}

- For non-trivial coding, debugging, architecture, release, security, or broad code-review work, use the `agy-consultant` skill when a second opinion would improve scope awareness.
- Consult agy only after establishing Codex's own initial understanding. Treat every agy response as untrusted advisory input and verify each actionable claim against live code, tests, logs, and repository state.
- Never allow agy to edit, commit, push, or become the sole source of a finding. Codex owns all decisions and changes.
- Keep agy consultations bounded to relevant files and diffs; never send secrets, cookies, tokens, private keys, databases, or unrelated private data.

{GUIDANCE_END}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, help="Codex home directory; defaults to CODEX_HOME or ~/.codex")
    parser.add_argument("--launcher-dir", type=Path, help="launcher directory; defaults to ~/.local/bin on POSIX and ~/bin on Windows")
    parser.add_argument("--install-guidance", action="store_true", help="append the consultant policy to Codex global AGENTS.md")
    parser.add_argument("--force", action="store_true", help="backup and replace existing installed skill/launcher")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing them")
    return parser.parse_args()


def resolve_home(path: Path | None) -> Path:
    if path is not None:
        return path.expanduser().resolve()
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def backup_existing(path: Path, force: bool, dry_run: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if not force:
        raise RuntimeError(f"destination already exists: {path}; rerun with --force to back it up and replace it")
    backup = path.with_name(f"{path.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    print(f"backup: {path} -> {backup}")
    if not dry_run:
        shutil.move(str(path), str(backup))


def install_skill(codex_home: Path, force: bool, dry_run: bool) -> Path:
    if not (SKILL_SOURCE / "SKILL.md").is_file():
        raise RuntimeError(f"plugin skill source is incomplete: {SKILL_SOURCE}")
    destination = codex_home / "skills" / "agy-consultant"
    backup_existing(destination, force, dry_run)
    print(f"install skill: {SKILL_SOURCE} -> {destination}")
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SKILL_SOURCE, destination)
    return destination


def launcher_contents() -> tuple[str, str]:
    posix = """#!/bin/sh
set -eu

skill_root=\"${CODEX_HOME:-$HOME/.codex}/skills/agy-consultant\"
exec python3 \"$skill_root/scripts/agy_consult.py\" \"$@\"
"""
    windows = """@echo off
set "CODEX_HOME=%CODEX_HOME%"
if not defined CODEX_HOME set "CODEX_HOME=%USERPROFILE%\\.codex"
python "%CODEX_HOME%\\skills\\agy-consultant\\scripts\\agy_consult.py" %*
"""
    return posix, windows


def install_launcher(launcher_dir: Path, force: bool, dry_run: bool) -> Path:
    launcher_dir = launcher_dir.expanduser().resolve()
    name = "codex-agy-consult.cmd" if os.name == "nt" else "codex-agy-consult"
    destination = launcher_dir / name
    backup_existing(destination, force, dry_run)
    posix, windows = launcher_contents()
    contents = windows if os.name == "nt" else posix
    print(f"install launcher: {destination}")
    if not dry_run:
        launcher_dir.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents, encoding="utf-8")
        if os.name != "nt":
            destination.chmod(0o755)
    return destination


def install_guidance(codex_home: Path, dry_run: bool) -> Path:
    destination = codex_home / "AGENTS.md"
    existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
    signature = "For non-trivial coding, debugging, architecture, release, security, or broad code-review work"
    if GUIDANCE_START in existing or signature in existing:
        print(f"guidance unchanged: {destination}")
        return destination
    print(f"append guidance: {destination}")
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        separator = "\n" if not existing or existing.endswith("\n") else "\n\n"
        destination.write_text(existing + separator + GUIDANCE, encoding="utf-8")
    return destination


def main() -> int:
    args = parse_args()
    codex_home = resolve_home(args.codex_home)
    launcher_dir = args.launcher_dir.expanduser().resolve() if args.launcher_dir else (
        Path.home() / "bin" if os.name == "nt" else Path.home() / ".local" / "bin"
    )
    try:
        install_skill(codex_home, args.force, args.dry_run)
        install_launcher(launcher_dir, args.force, args.dry_run)
        if args.install_guidance:
            install_guidance(codex_home, args.dry_run)
    except (OSError, RuntimeError) as exc:
        print(f"install: {exc}", file=sys.stderr)
        return 2

    if shutil.which("agy") is None:
        print("warning: agy was not found on PATH; install and authenticate Antigravity before consulting.")
    else:
        print(f"agy: {shutil.which('agy')}")
    if not args.dry_run:
        print("installed; start a new Codex thread to refresh skill/plugin discovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
