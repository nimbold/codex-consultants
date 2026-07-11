#!/usr/bin/env python3
"""Run a bounded, read-only Antigravity consultation for the current Git repo."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_MAX_BYTES = 120_000
DEFAULT_TIMEOUT_SECONDS = 300
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials.json",
    "cookies.json",
    "cookies.txt",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db")


def fail(message: str, code: int = 2) -> int:
    print(f"codex-agy-consult: {message}", file=sys.stderr)
    return code


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def find_repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("run this command from inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def relative_path(repo: Path, raw: str) -> Path:
    candidate = (repo / raw).resolve()
    try:
        return candidate.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path is outside the repository: {raw}") from exc


def is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    return name in SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES)


def safe_status(repo: Path) -> str:
    result = run_git(repo, ["status", "--short", "--untracked-files=all"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git status failed")

    lines = []
    for line in result.stdout.splitlines():
        path_text = line[3:].split(" -> ", 1)[-1].strip() if len(line) >= 3 else ""
        path = Path(path_text)
        lines.append("[sensitive path omitted]" if is_sensitive(path) else line)
    return "\n".join(lines) or "(clean or no status changes)"


def changed_paths(repo: Path) -> list[Path]:
    result = run_git(repo, ["diff", "--name-only", "-z", "HEAD", "--"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff path discovery failed")

    paths = []
    for item in result.stdout.split("\0"):
        if not item:
            continue
        path = Path(item)
        if not is_sensitive(path):
            paths.append(path)
    return paths


def read_selected_paths(repo: Path, raw_paths: list[str]) -> list[tuple[Path, str]]:
    selected = []
    seen = set()
    for raw in raw_paths:
        path = relative_path(repo, raw)
        if is_sensitive(path):
            raise ValueError(f"refusing to include sensitive path: {path}")
        if not path.is_file():
            raise ValueError(f"selected path is not a regular file: {path}")
        if path in seen:
            continue
        seen.add(path)
        selected.append((path, path.read_text(encoding="utf-8", errors="replace")))
    return selected


def build_diff(repo: Path, paths: list[Path]) -> str:
    if not paths:
        return "(no safe tracked diff supplied; include relevant files with --path)"
    result = run_git(
        repo,
        ["diff", "--no-ext-diff", "--no-textconv", "--unified=60", "HEAD", "--", *map(str, paths)],
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.stdout or "(no textual diff; changes may be binary or untracked)"


def build_payload(repo: Path, phase: str, task: str, max_bytes: int, extra_paths: list[str]) -> str:
    tracked_paths = changed_paths(repo)
    selected = read_selected_paths(repo, extra_paths)
    diff = build_diff(repo, tracked_paths)

    file_sections = []
    for path, content in selected:
        file_sections.append(f"--- BEGIN FILE {path} ---\n{content}\n--- END FILE {path} ---")
    files = "\n\n".join(file_sections) or "(no additional files supplied)"

    payload = f"""You are a read-only code consultant advising Codex.

Consultation phase: {phase}

Codex remains responsible for repository inspection, reasoning, edits, tests, and the final decision. Review only the task, repository status, selected files, and diff supplied below. Do not edit files, run commands, or claim to have inspected files, commits, logs, or tools that are not included. If the context is insufficient for a claim, write INSUFFICIENT_CONTEXT instead of guessing.

Return concise, evidence-based findings. For every finding include: severity, file/line or symbol, concrete evidence from the supplied context, impact, normal/worst-case scenario, confidence, and the next verification step. Separate observed facts from hypotheses. Do not produce an implementation patch unless Codex explicitly asks for one.

TASK FROM CODEX:
{task.strip()}

REPOSITORY STATUS:
{safe_status(repo)}

SELECTED CONTEXT FILES:
{files}

TRACKED DIFF:
{diff}
"""
    encoded = payload.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"consultation bundle is {len(encoded)} bytes, above the {max_bytes}-byte limit; narrow --path selections or raise --max-bytes deliberately"
        )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="task context; stdin is used when omitted")
    parser.add_argument("--phase", choices=("plan", "diff"), default="diff")
    parser.add_argument("--path", action="append", default=[], help="relevant repository file to include; repeatable")
    parser.add_argument("--agent", help="optional agy agent override")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_bytes <= 0 or args.timeout <= 0:
        return fail("--max-bytes and --timeout must be positive")
    task = args.prompt if args.prompt is not None else sys.stdin.read()
    if not task.strip():
        return fail("provide a consultation task as the positional prompt or on stdin")

    agy = shutil.which("agy")
    if not agy:
        return fail("agy was not found on PATH")

    try:
        repo = find_repo_root()
        payload = build_payload(repo, args.phase, task, args.max_bytes, args.path)
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc))

    command = [agy, "--mode", "plan", "--sandbox"]
    if args.agent:
        command.extend(["--agent", args.agent])
    command.extend(["--print", payload])

    try:
        with tempfile.TemporaryDirectory(prefix="codex-agy-consult-") as isolated_cwd:
            result = subprocess.run(
                command,
                cwd=isolated_cwd,
                text=True,
                capture_output=True,
                timeout=args.timeout,
                check=False,
                env=os.environ.copy(),
            )
    except subprocess.TimeoutExpired:
        return fail(f"agy timed out after {args.timeout} seconds", 124)
    except OSError as exc:
        return fail(f"could not start agy: {exc}", 127)

    if result.returncode != 0:
        detail = result.stderr.strip() or "agy returned no diagnostic"
        return fail(f"agy exited with status {result.returncode}: {detail}", result.returncode or 1)
    if not result.stdout.strip():
        detail = result.stderr.strip()
        suffix = f" Diagnostic: {detail}" if detail else ""
        return fail(f"agy returned an empty consultation response.{suffix}", 4)

    sys.stdout.write(result.stdout)
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
