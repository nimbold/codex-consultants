#!/usr/bin/env python3
"""Run a bounded, read-only Antigravity consultation for the current Git repo."""

from __future__ import annotations

import argparse
import os
import re
import signal
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
DEFAULT_MODEL = "Gemini 3.7 Flash (High)"
DEFAULT_PRINT_TIMEOUT = "120s"
MAX_MODELS = 2
MAX_FINDINGS = 4
MAX_REPORT_CHARS = 6_000
MAX_FULL_CONTEXT_FILE_BYTES = 24_000
MAX_DIRECTORY_FILES = 128
MAX_DIFF_BYTES = 64_000
MAX_MAX_BYTES = 2_000_000
MAX_TIMEOUT_SECONDS = 1_800
CONTEXT_BUDGET_RATIO = 0.35
DIFF_BUDGET_RATIO = 0.45
DIFF_CONTEXT_LINES = 20
LOCKFILE_NAMES = {
    "cargo.lock",
    "composer.lock",
    "go.sum",
    "gemfile.lock",
    "npm-shrinkwrap.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "yarn.lock",
    "bun.lockb",
}
MANIFEST_NAMES = {
    "cargo.toml",
    "manifest.json",
    "package.json",
    "pyproject.toml",
    "tauri.conf.json",
}
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
RETRYABLE_FAILURE_MARKERS = (
    "timed out",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "network",
    "rate limit",
    "too many requests",
    "status 429",
    "status 502",
    "status 503",
)
NON_RETRYABLE_FAILURE_MARKERS = (
    "permission",
    "context",
    "insufficient",
    "invalid",
    "not found",
    "not a regular",
)
ANSI_OSC_ESCAPE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


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


def process_group_kwargs() -> dict[str, int]:
    """Keep provider children in a killable process group on every platform."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen[str], *, force: bool = False) -> None:
    """Terminate a provider and descendants without signalling this wrapper."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 or "not found" in f"{result.stdout} {result.stderr}".lower():
            return
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        process.kill() if force else process.terminate()
    except ProcessLookupError:
        pass


def finish_terminated_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Drain a terminated process, with a hard fallback if descendants hold pipes."""
    try:
        stdout, stderr = process.communicate(timeout=2)
        return stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        terminate_process_tree(process, force=True)
        try:
            stdout, stderr = process.communicate(timeout=2)
            return stdout or "", stderr or ""
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            return "", ""


def run_bounded_process(
    command: list[str],
    *,
    cwd: str | os.PathLike[str],
    env: dict[str, str],
    timeout: float,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run a provider with timeout cleanup that also reaches its descendants."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        **process_group_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        stdout, stderr = finish_terminated_process(process)
        return subprocess.CompletedProcess(command, -1, stdout, stderr), True
    except KeyboardInterrupt:
        terminate_process_tree(process)
        finish_terminated_process(process)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout or "", stderr or ""), False


def bounded_git_output(repo: Path, args: list[str], limit: int = MAX_DIFF_BYTES) -> str | None:
    """Capture Git output without allowing a huge diff to exhaust memory."""
    process = subprocess.Popen(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **process_group_kwargs(),
    )
    output = bytearray()
    try:
        while process.stdout is not None:
            chunk = process.stdout.read1(min(64 * 1024, limit + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                terminate_process_tree(process, force=True)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                return None
        _, stderr = process.communicate()
    except KeyboardInterrupt:
        terminate_process_tree(process, force=True)
        finish_terminated_process(process)
        raise
    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "git diff failed")
    return bytes(output).decode("utf-8", errors="replace")


def sanitize_text(text: str) -> str:
    """Remove terminal control sequences before reports reach Codex or logs."""
    text = ANSI_OSC_ESCAPE.sub("", str(text))
    text = ANSI_ESCAPE.sub("", text)
    return "".join(character for character in text if character in "\r\n\t" or ord(character) >= 32)


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


def is_lockfile(path: Path) -> bool:
    return path.name.lower() in LOCKFILE_NAMES


def utf8_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def path_priority(path: Path, explicit_paths: set[Path]) -> int:
    """Prefer explicit contract files and manifests when the bundle needs pruning."""
    score = 1_000 if path in explicit_paths else 0
    name = path.name.lower()
    if name in MANIFEST_NAMES:
        score += 300
    if len(path.parts) >= 2 and path.parts[0] == ".github" and path.parts[1] == "workflows":
        score += 200
    if path.suffix.lower() in {".toml", ".json", ".yaml", ".yml", ".rs", ".ts", ".tsx", ".js"}:
        score += 50
    return score


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
    seen = set()
    for item in result.stdout.split("\0"):
        if not item:
            continue
        path = Path(item)
        if not is_sensitive(path) and path not in seen:
            paths.append(path)
            seen.add(path)

    untracked = run_git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--"])
    if untracked.returncode != 0:
        raise RuntimeError(untracked.stderr.strip() or "untracked path discovery failed")
    for item in untracked.stdout.split("\0"):
        if not item:
            continue
        path = Path(item)
        if not is_sensitive(path) and path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def expand_requested_paths(repo: Path, raw_paths: list[str], notes: list[str]) -> list[Path]:
    """Resolve explicit files and bounded directory selections safely."""
    expanded = []
    seen = set()
    for raw in raw_paths:
        raw_candidate = repo / raw
        if raw_candidate.is_symlink():
            raise ValueError(f"refusing to include symlink path: {raw}")
        path = relative_path(repo, raw)
        if is_sensitive(path):
            raise ValueError(f"refusing to include sensitive path: {path}")
        absolute = (repo / path).resolve()
        if absolute.is_file():
            candidates = [path]
        elif absolute.is_dir():
            candidates = []
            sensitive_count = 0
            outside_count = 0
            capped = False
            for candidate in sorted(absolute.rglob("*")):
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                try:
                    relative = candidate.resolve().relative_to(repo)
                except ValueError:
                    outside_count += 1
                    continue
                if is_sensitive(relative):
                    sensitive_count += 1
                    continue
                candidates.append(relative)
                if len(candidates) >= MAX_DIRECTORY_FILES:
                    capped = True
                    break
            if capped:
                notes.append(
                    f"{path}: directory selection capped at {MAX_DIRECTORY_FILES} files"
                )
            if sensitive_count:
                notes.append(f"{path}: {sensitive_count} sensitive path(s) omitted by preflight")
            if outside_count:
                notes.append(f"{path}: {outside_count} outside-repository path(s) omitted by preflight")
            if not candidates:
                notes.append(f"{path}: directory contains no regular files")
        else:
            raise ValueError(f"selected path is not a regular file or directory: {path}")

        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded


def read_bounded_text(path: Path) -> str:
    """Avoid loading files that preflight will reject at full size."""
    with path.open("rb") as stream:
        content = stream.read(MAX_FULL_CONTEXT_FILE_BYTES + 1)
    return content.decode("utf-8", errors="replace")


def read_selected_paths(
    repo: Path, raw_paths: list[str], notes: list[str] | None = None
) -> list[tuple[Path, str]]:
    notes = notes if notes is not None else []
    selected = []
    seen = set()
    for path in expand_requested_paths(repo, raw_paths, notes):
        absolute = (repo / path).resolve()
        if path in seen:
            continue
        seen.add(path)
        selected.append((path, read_bounded_text(absolute)))
    return selected


def build_path_diff(repo: Path, path: Path, unified: int = DIFF_CONTEXT_LINES) -> str:
    tracked = run_git(repo, ["ls-files", "--error-unmatch", "--", str(path)])
    if tracked.returncode != 0:
        absolute = (repo / path).resolve()
        if not absolute.is_file() or absolute.is_symlink():
            return ""
        with absolute.open("rb") as stream:
            raw_content = stream.read(MAX_DIFF_BYTES + 1)
        if len(raw_content) > MAX_DIFF_BYTES:
            return f"diff --git a/{path} b/{path}\n[diff omitted: file exceeds {MAX_DIFF_BYTES} bytes]\n"
        content = raw_content.decode("utf-8", errors="replace")
        if "\x00" in content:
            return ""
        lines = content.splitlines(keepends=True)
        if content and not lines:
            lines = [content]
        body = "".join(f"+{line}" for line in lines)
        if content and not content.endswith("\n"):
            body += "\n\\ No newline at end of file\n"
        line_count = len(lines)
        return (
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{path}\n"
            f"@@ -0,0 +1,{line_count} @@\n"
            f"{body}"
        )
    result = bounded_git_output(
        repo,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            f"--unified={unified}",
            "HEAD",
            "--",
            str(path),
        ],
    )
    if result is None:
        return f"diff --git a/{path} b/{path}\n[diff omitted: generated diff exceeds {MAX_DIFF_BYTES} bytes]\n"
    return result


def file_section(path: Path, content: str) -> str:
    return f"--- BEGIN FILE {path} ---\n{content}\n--- END FILE {path} ---"


def select_context_files(
    selected: list[tuple[Path, str]],
    explicit_paths: set[Path],
    budget: int,
    notes: list[str],
) -> list[tuple[Path, str]]:
    candidates = []
    for index, (path, content) in enumerate(selected):
        if is_lockfile(path):
            notes.append(f"{path}: full lockfile omitted by preflight")
            continue
        size = utf8_bytes(content)
        if size > MAX_FULL_CONTEXT_FILE_BYTES:
            notes.append(
                f"{path}: full file omitted by preflight ({size} bytes; limit {MAX_FULL_CONTEXT_FILE_BYTES}); use a narrower symbol/path"
            )
            continue
        candidates.append((path_priority(path, explicit_paths), index, path, content))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen = []
    used = 0
    for _, _, path, content in candidates:
        cost = utf8_bytes(file_section(path, content))
        if used + cost > budget:
            notes.append(f"{path}: omitted by preflight context budget")
            continue
        chosen.append((path, content))
        used += cost
    chosen.sort(key=lambda item: next(index for index, candidate in enumerate(selected) if candidate[0] == item[0]))
    return chosen


def select_diff(
    repo: Path,
    tracked_paths: list[Path],
    explicit_paths: set[Path],
    budget: int,
    notes: list[str],
) -> str:
    candidates = []
    for path in tracked_paths:
        if is_lockfile(path):
            notes.append(f"{path}: lockfile diff omitted by preflight")
            continue
        candidates.append((path_priority(path, explicit_paths), str(path), path))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen = []
    used = 0
    for _, _, path in candidates:
        diff = build_path_diff(repo, path, DIFF_CONTEXT_LINES)
        if not diff:
            continue
        size = utf8_bytes(diff)
        if used + size <= budget:
            chosen.append(diff)
            used += size
            continue

        compact_diff = build_path_diff(repo, path, unified=0)
        compact_size = utf8_bytes(compact_diff)
        if compact_diff and used + compact_size <= budget:
            chosen.append(compact_diff)
            used += compact_size
        else:
            notes.append(f"{path}: diff omitted by preflight diff budget")

    return "\n\n".join(chosen) or "(no safe working-tree diff supplied after preflight)"


def build_payload(
    repo: Path,
    phase: str,
    task: str,
    max_bytes: int,
    extra_paths: list[str],
) -> tuple[str, list[tuple[Path, str]]]:
    repo = repo.resolve()
    notes = []
    selected = read_selected_paths(repo, extra_paths, notes)
    explicit_paths = {path for path, _ in selected}
    context_budget = int(max_bytes * (0.70 if phase == "plan" else CONTEXT_BUDGET_RATIO))
    context_files = select_context_files(selected, explicit_paths, context_budget, notes)
    if phase == "plan":
        diff = "(tracked diff omitted for plan phase; include relevant files explicitly)"
    else:
        tracked_paths = changed_paths(repo)
        diff = select_diff(
            repo,
            tracked_paths,
            explicit_paths,
            int(max_bytes * DIFF_BUDGET_RATIO),
            notes,
        )

    file_sections = [file_section(path, content) for path, content in context_files]
    files = "\n\n".join(file_sections) or "(no additional files supplied)"
    preflight_notes = "\n".join(f"- {note}" for note in notes) or "(none)"

    payload = f"""You are a read-only code consultant advising Codex.

Consultation phase: {phase}

Codex remains responsible for repository inspection, reasoning, edits, tests, and the final decision. Review only the task, repository status, selected files, and diff supplied below. Do not edit files, run commands, or claim to have inspected files, commits, logs, or tools that are not included. If the context is insufficient for a claim, write INSUFFICIENT_CONTEXT instead of guessing.

Return one compact report only. Do not restate the task, files, or your reasoning. Use exactly these line formats:
REPORT: <one-sentence overall conclusion>
FINDING: <severity> | <FACT or HYPOTHESIS> | <file/line or symbol> | <concrete evidence> | <impact> | <normal/worst-case scenario> | <confidence> | <next verification step>
UNCERTAINTY: <one sentence, only when needed>
If there are no actionable findings, return NO_ACTIONABLE_FINDINGS instead of inventing one. Use at most four FINDING lines, keep each line under 600 characters, and keep the complete response under 2,500 characters. Do not produce an implementation patch unless Codex explicitly asks for one.

TASK FROM CODEX:
{task.strip()}

REPOSITORY STATUS:
{safe_status(repo)}

SELECTED CONTEXT FILES:
{files}

CONTEXT PREFLIGHT NOTES:
{preflight_notes}

TRACKED DIFF:
{diff}
"""
    encoded = payload.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"consultation bundle is {len(encoded)} bytes, above the {max_bytes}-byte limit; narrow --path selections or raise --max-bytes deliberately"
        )
    return payload, context_files


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
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
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
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"maximum consultation bundle size in bytes (default: {DEFAULT_MAX_BYTES}; max: {MAX_MAX_BYTES})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"provider timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}; max: {MAX_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"retry transient agy failures (default: {DEFAULT_RETRIES}; max: 2)",
    )
    return parser.parse_args()


def compact_diagnostic(stderr: str, limit: int = 2_000) -> str:
    detail = sanitize_text(stderr).strip()
    if len(detail) <= limit:
        return detail
    return "..." + detail[-limit:]


def should_retry(failure: str) -> bool:
    """Retry only failures that may change without changing the request."""
    lowered = failure.lower()
    if "returned an empty consultation response" in lowered:
        return not any(marker in lowered for marker in NON_RETRYABLE_FAILURE_MARKERS)
    return any(marker in lowered for marker in RETRYABLE_FAILURE_MARKERS)


def compact_report(text: str, max_findings: int = MAX_FINDINGS, max_chars: int = MAX_REPORT_CHARS) -> str:
    """Keep only the bounded report contract that Codex needs to review."""
    lines = []
    for raw_line in sanitize_text(text).replace("\r\n", "\n").splitlines():
        line = re.sub(r"^\s*[-*]\s+", "", raw_line.strip().strip("`")).strip()
        if line:
            lines.append(re.sub(r"\s+", " ", line))

    structured = []
    findings = 0
    for line in lines:
        upper = line.upper()
        if upper.startswith("FINDING:"):
            if findings < max_findings:
                structured.append(line)
                findings += 1
        elif upper.startswith(("REPORT:", "UNCERTAINTY:", "NO_ACTIONABLE_FINDINGS")):
            structured.append(line)

    if structured:
        report = "\n".join(structured)
    else:
        report = "UNSTRUCTURED_REPORT: " + " ".join(lines)

    if len(report) <= max_chars:
        return report
    marker = "\n[report clipped by wrapper; verify omitted detail against supplied context]"
    return report[: max_chars - len(marker)].rstrip() + marker


def main() -> int:
    args = parse_args()
    if args.max_bytes <= 0 or args.max_bytes > MAX_MAX_BYTES:
        return fail(f"--max-bytes must be between 1 and {MAX_MAX_BYTES}")
    if args.timeout <= 0 or args.timeout > MAX_TIMEOUT_SECONDS:
        return fail(f"--timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds")
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
                    result, timed_out = run_bounded_process(
                        command,
                        cwd=isolated_cwd,
                        env=os.environ.copy(),
                        timeout=remaining,
                    )
            except subprocess.TimeoutExpired:
                model_failure = f"timed out after {args.timeout} seconds"
            except OSError as exc:
                model_failure = f"could not start agy: {exc}"
            else:
                if timed_out:
                    model_failure = f"timed out after {args.timeout} seconds"
                elif result.returncode != 0:
                    detail = compact_diagnostic(result.stderr) or "agy returned no diagnostic"
                    model_failure = f"exited with status {result.returncode}: {detail}"
                elif not result.stdout.strip():
                    detail = compact_diagnostic(result.stderr)
                    suffix = f" Diagnostic: {detail}" if detail else ""
                    model_failure = f"returned an empty consultation response.{suffix}"
                else:
                    responses.append((model, compact_report(result.stdout), compact_diagnostic(result.stderr)))
                    model_failure = None
                    break

            if attempt < args.retries and should_retry(model_failure or ""):
                time.sleep(min(RETRY_DELAY_SECONDS, max(0.0, deadline - time.monotonic())))
            else:
                break

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
