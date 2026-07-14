#!/usr/bin/env python3
"""Run a bounded, read-only Antigravity consultation for the current Git repo."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


DEFAULT_MAX_BYTES = 80_000
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_RETRIES = 1
RETRY_DELAY_SECONDS = 2.0
DEFAULT_MODEL = "Gemini 3.5 Flash (High)"
DEFAULT_PRINT_TIMEOUT = "120s"
MAX_MODELS = 3
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


def build_payload(
    repo: Path,
    phase: str,
    task: str,
    max_bytes: int,
    extra_paths: list[str],
) -> tuple[str, list[tuple[Path, str]]]:
    selected = read_selected_paths(repo, extra_paths)
    workspace_files = list(selected)
    if phase == "plan":
        diff = "(tracked diff omitted for plan phase; include relevant files explicitly)"
    else:
        tracked_paths = changed_paths(repo)
        diff = build_diff(repo, tracked_paths)
        selected_paths = {path for path, _ in workspace_files}
        for path in tracked_paths:
            if path in selected_paths or is_sensitive(path) or not path.is_file():
                continue
            workspace_files.append((path, path.read_text(encoding="utf-8", errors="replace")))

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
    return payload, workspace_files


def materialize_selected_files(workspace: Path, selected: list[tuple[Path, str]]) -> None:
    """Expose only the explicitly selected files to agy tool calls."""
    for path, content in selected:
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def build_command(agy: str, args: argparse.Namespace, payload: str, model: str) -> list[str]:
    command = [
        agy,
        "--mode",
        "plan",
        "--sandbox",
        "--model",
        model,
        "--print-timeout",
        args.print_timeout,
    ]
    if args.agent:
        command.extend(["--agent", args.agent])
    command.extend(["--print", payload])
    return command


def resolve_models(args: argparse.Namespace) -> list[str]:
    requested = args.models or [DEFAULT_MODEL]
    models = []
    for raw_model in requested:
        model = raw_model.strip()
        if not model:
            raise ValueError("--model values must not be empty")
        if model not in models:
            models.append(model)
    if len(models) > MAX_MODELS:
        raise ValueError(f"use at most {MAX_MODELS} models per consultation")
    return models


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="task context; stdin is used when omitted")
    parser.add_argument("--phase", choices=("plan", "diff"), default="diff")
    parser.add_argument("--path", action="append", default=[], help="relevant repository file to include; repeatable")
    parser.add_argument("--agent", help="optional agy agent-script override; use --model for model selection")
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        help=f"agy model label; repeat for independent opinions (default: {DEFAULT_MODEL}; max: {MAX_MODELS})",
    )
    parser.add_argument(
        "--print-timeout",
        default=DEFAULT_PRINT_TIMEOUT,
        help=f"agy print-mode timeout duration (default: {DEFAULT_PRINT_TIMEOUT})",
    )
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"retry transient agy failures (default: {DEFAULT_RETRIES}; max: 2)",
    )
    return parser.parse_args()


def compact_diagnostic(stderr: str, limit: int = 2_000) -> str:
    detail = stderr.strip()
    if len(detail) <= limit:
        return detail
    return "..." + detail[-limit:]


def main() -> int:
    args = parse_args()
    if args.max_bytes <= 0 or args.timeout <= 0:
        return fail("--max-bytes and --timeout must be positive")
    if args.retries < 0 or args.retries > 2:
        return fail("--retries must be between 0 and 2")
    try:
        models = resolve_models(args)
    except ValueError as exc:
        return fail(str(exc))
    task = args.prompt if args.prompt is not None else sys.stdin.read()
    if not task.strip():
        return fail("provide a consultation task as the positional prompt or on stdin")

    agy = shutil.which("agy")
    if not agy:
        return fail("agy was not found on PATH")

    try:
        repo = find_repo_root()
        payload, selected = build_payload(repo, args.phase, task, args.max_bytes, args.path)
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc))

    responses = []
    unavailable = []
    for model in models:
        command = build_command(agy, args, payload, model)
        deadline = time.monotonic() + args.timeout
        model_failure = None
        for attempt in range(args.retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                model_failure = f"timed out after {args.timeout} seconds"
                break
            try:
                with tempfile.TemporaryDirectory(prefix="codex-agy-consult-") as isolated_cwd:
                    materialize_selected_files(Path(isolated_cwd), selected)
                    result = subprocess.run(
                        command,
                        cwd=isolated_cwd,
                        text=True,
                        capture_output=True,
                        timeout=remaining,
                        check=False,
                        env=os.environ.copy(),
                    )
            except subprocess.TimeoutExpired:
                model_failure = f"timed out after {args.timeout} seconds"
            except OSError as exc:
                model_failure = f"could not start agy: {exc}"
            else:
                if result.returncode != 0:
                    detail = compact_diagnostic(result.stderr) or "agy returned no diagnostic"
                    model_failure = f"exited with status {result.returncode}: {detail}"
                elif not result.stdout.strip():
                    detail = compact_diagnostic(result.stderr)
                    suffix = f" Diagnostic: {detail}" if detail else ""
                    model_failure = f"returned an empty consultation response.{suffix}"
                else:
                    responses.append((model, result.stdout, compact_diagnostic(result.stderr)))
                    model_failure = None
                    break

            if attempt < args.retries:
                time.sleep(min(RETRY_DELAY_SECONDS, max(0.0, deadline - time.monotonic())))

        if model_failure:
            unavailable.append(f"{model}: {model_failure}")

    if not responses:
        detail = "; ".join(unavailable) or "no response"
        return fail(f"all agy consultations unavailable: {detail}", 4)

    if len(responses) == 1:
        _, stdout, stderr = responses[0]
        sys.stdout.write(stdout)
        if stderr:
            print(stderr, file=sys.stderr)
    else:
        for model, stdout, stderr in responses:
            print(f"=== agy consultation: {model} ===")
            sys.stdout.write(stdout)
            if not stdout.endswith("\n"):
                print()
            if stderr:
                print(f"[{model}] {stderr}", file=sys.stderr)

    if unavailable:
        print(
            "codex-agy-consult: unavailable model(s): " + "; ".join(unavailable),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
