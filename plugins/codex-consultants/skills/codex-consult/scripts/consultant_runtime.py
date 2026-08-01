#!/usr/bin/env python3
"""Shared job runtime for the Codex Consultants provider adapters.

The runtime deliberately keeps provider clients outside the control plane. It
owns bounded job state, isolation, fan-out, cancellation, and result handling;
the existing Agy and OpenCode adapters remain responsible for their
provider-specific safety and request configuration.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = PLUGIN_ROOT / "skills" if (PLUGIN_ROOT / "skills").is_dir() else PLUGIN_ROOT
STATE_ENV = "CODEX_CONSULT_STATE_DIR"
SESSION_ENV = "CODEX_CONSULT_SESSION_ID"
PROVIDER_ORDER = ("agy", "opencode")
PROVIDER_SCRIPTS = {
    "agy": SKILLS_ROOT / "agy-consult" / "scripts" / "agy_consult.py",
    "opencode": SKILLS_ROOT / "opencode-consult" / "scripts" / "opencode_consult.py",
}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "cancelling"}
MAX_STORED_JOBS = 32
MAX_LOG_BYTES = 96_000
QUEUE_STALE_SECONDS = 60
MAX_TARGET_CONTEXT_BYTES = 48_000
SENSITIVE_NAMES = {".env", ".env.local", ".env.production", ".env.development", "credentials.json", "cookies.json", "cookies.txt"}
LOCKFILE_NAMES = {"cargo.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "gemfile.lock", "go.sum"}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db")
DEFAULT_REVIEW_PROMPT = (
    "Perform a normal, bounded, read-only code review of the supplied repository context. "
    "Report only material, actionable correctness, regression, security, reliability, or "
    "maintainability issues. Separate verified facts from uncertainty and include a precise "
    "file or evidence reference for every finding. Do not edit files or assume missing context."
)
DEFAULT_ADVERSARIAL_PROMPT = (
    "Perform an adversarial, bounded, read-only code review of the supplied repository context. "
    "Challenge the implementation's assumptions, failure modes, races, recovery, malformed "
    "input handling, security boundaries, and platform behavior. Report only material, "
    "actionable issues, distinguish normal usage from worst-case behavior, and cite evidence. "
    "Do not edit files or assume missing context.\n\nFocus: {focus}"
)
SENSITIVE_DIAGNOSTIC = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization|bearer)\s*[:=]\s*([^\s,;]+)"
)
KNOWN_TOKEN = re.compile(r"\b(?:sk|gh[pousr]|xox[baprs])_[A-Za-z0-9_-]+\b")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def session_id(env: dict[str, str] | None = None) -> str | None:
    values = env or os.environ
    for key in (SESSION_ENV, "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        value = values.get(key, "").strip()
        if value:
            return value[:256]
    return None


def find_repo_root(cwd: Path | None = None) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd or Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("run this command from inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def is_sensitive_path(path: str) -> bool:
    candidate = Path(path)
    name = candidate.name.lower()
    return name in SENSITIVE_NAMES or name in LOCKFILE_NAMES or name.endswith(SENSITIVE_SUFFIXES)


def bounded_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    marker = "\n[context truncated]"
    if limit <= len(marker.encode("utf-8")):
        return marker.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    available = max(0, limit - len(marker.encode("utf-8")))
    return encoded[:available].decode("utf-8", errors="ignore") + marker


def safe_status_text(repo: Path) -> str:
    result = run_git(repo, ["status", "--short", "--untracked-files=all"])
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "could not read working-tree status")
    lines = []
    for line in result.stdout.splitlines():
        path_text = line[3:].split(" -> ", 1)[-1].strip() if len(line) >= 3 else ""
        lines.append("[sensitive path omitted]" if is_sensitive_path(path_text) else line)
    return "\n".join(lines) or "(clean working tree)"


def review_target_context(repo: Path, scope: str, base: str | None, max_bytes: int = 80_000) -> str:
    """Build a bounded target summary using literal git arguments only."""
    status_text = safe_status_text(repo)
    dirty = bool(status_text and status_text != "(clean working tree)")
    selected_scope = scope
    if selected_scope == "auto":
        selected_scope = "working-tree" if dirty or not base else "branch"

    if selected_scope == "working-tree":
        return bounded_text(
            f"Review target: working tree\nWorking-tree status:\n{status_text}",
            min(MAX_TARGET_CONTEXT_BYTES, max(1, int(max_bytes))),
        )
    if not base:
        raise ValueError("branch review requires --base <ref>")
    if base.startswith("-"):
        raise ValueError("review base must be a Git ref, not an option")

    verified = run_git(repo, ["rev-parse", "--verify", f"{base}^{{commit}}"])
    if verified.returncode != 0:
        raise ValueError(f"could not resolve review base {base!r}")
    merge_base = run_git(repo, ["merge-base", base, "HEAD"])
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise ValueError(f"could not compute a merge base for {base!r} and HEAD")
    commit_range = f"{merge_base.stdout.strip()}..HEAD"
    target_budget = min(MAX_TARGET_CONTEXT_BYTES, max(1, int(max_bytes * 0.35)))
    names = run_git(repo, ["diff", "--name-only", "-z", commit_range])
    if names.returncode != 0:
        raise ValueError(names.stderr.strip() or "could not enumerate branch changes")

    safe_paths = [
        raw_path
        for raw_path in names.stdout.split("\0")
        if raw_path and not is_sensitive_path(raw_path)
    ]
    parts = [
        f"Review target: branch against {base}",
        f"Merge base: {merge_base.stdout.strip()}",
        "Commit log:",
        run_git(repo, ["log", "--oneline", "--decorate", commit_range]).stdout.strip() or "(no commits)",
        "Diff stat:",
        run_git(repo, ["diff", "--stat", commit_range, "--", *safe_paths]).stdout.strip() or "(no changes)",
    ]
    used = sum(len(part.encode("utf-8")) for part in parts)
    omitted = []
    for raw_path in names.stdout.split("\0"):
        if not raw_path:
            continue
        if is_sensitive_path(raw_path):
            omitted.append("[sensitive path omitted]: omitted by safety preflight")
            continue
        diff = run_git(repo, ["diff", "--no-ext-diff", "--no-textconv", "--unified=20", commit_range, "--", raw_path])
        if diff.returncode != 0:
            omitted.append(f"{raw_path}: diff unavailable")
            continue
        encoded = diff.stdout.encode("utf-8", errors="replace")
        if used + len(encoded) > target_budget:
            omitted.append(f"{raw_path}: omitted by branch context budget")
            continue
        parts.extend([f"Diff for {raw_path}:", diff.stdout])
        used += len(encoded)
    if omitted:
        parts.extend(["Preflight omissions:", *omitted])
    return bounded_text("\n".join(parts), target_budget)


def state_dir(repo: Path) -> Path:
    override = os.environ.get(STATE_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()

    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    digest = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:16]
    slug = "".join(character if character.isalnum() or character in "._-" else "-" for character in repo.name)
    return codex_home / "state" / "codex-consultants" / f"{slug or 'workspace'}-{digest}"


def jobs_dir(repo: Path) -> Path:
    path = state_dir(repo) / "jobs"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def job_path(repo: Path, job_id: str) -> Path:
    return jobs_dir(repo) / f"{job_id}.json"


@contextmanager
def state_lock(repo: Path):
    """Serialize read-modify-write state transitions across threads/processes."""
    lock_path = jobs_dir(repo) / ".state.lock"
    with _STATE_THREAD_LOCK:
        with lock_path.open("a+b") as lock:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock.seek(0)
                    lock.write(b"0")
                    lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - platform-specific fallback
                pass
            try:
                yield
            finally:
                if os.name == "nt":
                    try:
                        import msvcrt

                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    except ImportError:
                        pass
                else:
                    try:
                        import fcntl

                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                    except ImportError:
                        pass


_STATE_THREAD_LOCK = threading.RLock()
_LOG_THREAD_LOCK = threading.Lock()


def spec_path(repo: Path, job_id: str) -> Path:
    return jobs_dir(repo) / f"{job_id}.spec.json"


def log_path(repo: Path, job_id: str) -> Path:
    return jobs_dir(repo) / f"{job_id}.log"


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def write_private_marker(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_job(repo: Path, job_id: str) -> dict[str, Any] | None:
    return read_json(job_path(repo, job_id))


def apply_job_patch(repo: Path, job_id: str, current: dict[str, Any], **patch: Any) -> dict[str, Any]:
    current.update(patch)
    current["updatedAt"] = now_iso()
    atomic_write_json(job_path(repo, job_id), current)
    return current


def update_job(repo: Path, job_id: str, **patch: Any) -> dict[str, Any]:
    with state_lock(repo):
        current = load_job(repo, job_id)
        if current is None:
            raise RuntimeError(f"job {job_id} no longer exists")
        return apply_job_patch(repo, job_id, current, **patch)


def append_log(path: Path, message: str) -> None:
    message = str(message).strip()
    if not message:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with _LOG_THREAD_LOCK:
            current_size = path.stat().st_size if path.exists() else 0
            remaining = MAX_LOG_BYTES - current_size
            if remaining <= 0:
                return
            entry = f"[{now_iso()}] {message[:2_000]}\n"
            encoded = entry.encode("utf-8")
            if len(encoded) > remaining:
                entry = encoded[:remaining].decode("utf-8", errors="ignore")
            with path.open("a", encoding="utf-8") as stream:
                stream.write(entry)
    except OSError:
        pass


def provider_names(values: list[str] | None, default: str = "all") -> list[str]:
    requested = values or [default]
    names: list[str] = []
    for raw in requested:
        for name in raw.split(","):
            name = name.strip().lower()
            if not name:
                continue
            if name == "all":
                expanded = list(PROVIDER_ORDER)
            elif name in PROVIDER_ORDER:
                expanded = [name]
            else:
                raise ValueError(f"unknown provider {name!r}; use agy, opencode, or all")
            for provider in expanded:
                if provider not in names:
                    names.append(provider)
    if not names:
        raise ValueError("at least one provider is required")
    return names


def prompt_template(kind: str) -> str:
    defaults = {
        "review": DEFAULT_REVIEW_PROMPT,
        "adversarial-review": DEFAULT_ADVERSARIAL_PROMPT,
    }
    fallback = defaults[kind]
    path = SKILLS_ROOT / "codex-consult" / "prompts" / f"{kind}.md"
    try:
        contents = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return fallback
    return contents or fallback


def build_provider_command(provider: str, task: str, options: argparse.Namespace, repo: Path) -> list[str]:
    script = PROVIDER_SCRIPTS[provider]
    if not script.is_file():
        raise RuntimeError(f"provider adapter is missing: {script}")

    command = [
        sys.executable,
        str(script),
        "--phase",
        options.phase,
        "--max-bytes",
        str(options.max_bytes),
        "--timeout",
        str(options.timeout),
        "--retries",
        str(options.retries),
    ]
    if provider == "agy":
        if options.print_timeout:
            command.extend(["--print-timeout", options.print_timeout])
        if options.agent:
            command.extend(["--agent", options.agent])
    elif provider == "opencode" and options.variant:
        command.extend(["--variant", options.variant])

    for model in options.model or []:
        command.extend(["--model", model])
    for path in options.path or []:
        command.extend(["--path", path])
    command.extend(["--", task])
    return command


def compact(text: str, limit: int = 2_000) -> str:
    text = " ".join(str(text).strip().split())
    text = SENSITIVE_DIAGNOSTIC.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = KNOWN_TOKEN.sub("[redacted-token]", text)
    return text if len(text) <= limit else text[-limit:]


def process_group_kwargs() -> dict[str, Any]:
    """Create an isolated process group on both POSIX and Windows."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def terminate_pid(pid: int | None, *, force: bool = False) -> bool:
    try:
        pid = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        return False
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False)
        return result.returncode == 0 or "not found" in f"{result.stdout} {result.stderr}".lower()
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return False
    except OSError:
        return False


def pid_alive(pid: int | None) -> bool:
    try:
        pid = int(pid) if pid is not None else 0
    except (TypeError, ValueError):
        return False
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def wait_for_exit(pid: int | None, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not pid_alive(pid)


def create_job(repo: Path, kind: str, providers: list[str], task: str, options: argparse.Namespace) -> str:
    job_id = f"consult-{int(time.time())}-{secrets.token_hex(4)}"
    created = now_iso()
    record = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "phase": "queued",
        "providers": providers,
        "workspaceRoot": str(repo),
        "sessionId": session_id(),
        "createdAt": created,
        "updatedAt": created,
        "startedAt": None,
        "completedAt": None,
        "pid": None,
        "providerPids": {},
        "logFile": str(log_path(repo, job_id)),
        "providerResults": {},
        "reports": [],
    }
    with state_lock(repo):
        atomic_write_json(job_path(repo, job_id), record)
        atomic_write_json(
            spec_path(repo, job_id),
            {
                "task": task,
                "cwd": str(repo),
                "phase": options.phase,
                "max_bytes": options.max_bytes,
                "timeout": options.timeout,
                "retries": options.retries,
                "model": options.model or [],
                "path": options.path or [],
                "variant": options.variant,
                "print_timeout": options.print_timeout,
                "agent": options.agent,
                "scope": options.scope,
                "base": options.base,
            },
        )
        log_path(repo, job_id).write_text("", encoding="utf-8")
        os.chmod(log_path(repo, job_id), 0o600)
        prune_jobs(repo)
    return job_id


def prune_jobs(repo: Path) -> None:
    jobs = []
    for path in jobs_dir(repo).glob("consult-*.json"):
        job = read_json(path)
        if job is not None:
            jobs.append(job)
    terminal = sorted(
        (job for job in jobs if job.get("status") in TERMINAL_STATUSES),
        key=lambda item: item.get("updatedAt", ""),
        reverse=True,
    )
    for job in terminal[MAX_STORED_JOBS:]:
        for path in (job_path(repo, job["id"]), spec_path(repo, job["id"]), log_path(repo, job["id"]), cancel_marker(repo, job["id"])):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def load_spec(repo: Path, job_id: str) -> argparse.Namespace:
    raw = read_json(spec_path(repo, job_id))
    if raw is None:
        raise RuntimeError(f"job specification for {job_id} is missing or invalid")
    return argparse.Namespace(**raw)


def run_provider(
    provider: str,
    task: str,
    options: argparse.Namespace,
    repo: Path,
    active: dict[str, subprocess.Popen[str]],
    active_lock: threading.Lock,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    if cancel_event.is_set() or cancel_marker(repo, options.job_id).exists():
        return {"status": "cancelled", "error": "cancellation requested before provider start"}
    command = build_provider_command(provider, task, options, repo)
    append_log(log_path(repo, options.job_id), f"{provider}: starting")
    try:
        process = subprocess.Popen(
            command,
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
            **process_group_kwargs(),
        )
    except OSError as exc:
        return {"status": "failed", "error": f"could not start {provider}: {exc}"}

    with active_lock:
        active[provider] = process
        provider_pids = {name: child.pid for name, child in active.items()}
    try:
        update_job(repo, options.job_id, providerPids=provider_pids)
    except RuntimeError as exc:
        terminate_pid(process.pid)
        with active_lock:
            active.pop(provider, None)
        return {"status": "failed", "error": f"could not record {provider} process: {exc}"}
    if cancel_event.is_set() or cancel_marker(repo, options.job_id).exists():
        terminate_pid(process.pid)
        return {"status": "cancelled", "error": "cancellation requested during provider start"}
    try:
        stdout, stderr = process.communicate(timeout=max(30, int(options.timeout) + 30))
    except subprocess.TimeoutExpired:
        terminate_pid(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            terminate_pid(process.pid, force=True)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
        result = {"status": "failed", "error": f"timed out after {options.timeout} seconds"}
    else:
        report = stdout.strip()
        diagnostic = compact(stderr)
        if process.returncode == 0 and report:
            result = {"status": "completed", "report": report}
            if diagnostic:
                result["diagnostic"] = diagnostic
        else:
            result = {
                "status": "failed",
                "exitCode": process.returncode,
                "error": diagnostic or "provider returned no report",
            }
    finally:
        with active_lock:
            active.pop(provider, None)
            provider_pids = {name: child.pid for name, child in active.items()}
        try:
            update_job(repo, options.job_id, providerPids=provider_pids)
        except RuntimeError:
            pass

    if result["status"] == "completed":
        append_log(log_path(repo, options.job_id), f"{provider}: completed")
    else:
        append_log(log_path(repo, options.job_id), f"{provider}: {result.get('error', 'failed')}")
    return result


def claim_worker(repo: Path, job_id: str) -> dict[str, Any] | None:
    """Atomically claim a queued job, rejecting duplicate or cancelled workers."""
    with state_lock(repo):
        job = load_job(repo, job_id)
        if job is None:
            raise RuntimeError(f"job {job_id} was not found")
        if job.get("status") in TERMINAL_STATUSES:
            return None
        if job.get("status") != "queued":
            raise RuntimeError(f"job {job_id} is already {job.get('status', 'active')}")
        if cancel_marker(repo, job_id).exists():
            return apply_job_patch(
                repo,
                job_id,
                job,
                status="cancelled",
                phase="cancelled",
                completedAt=now_iso(),
                pid=None,
                providerPids={},
            )
        return apply_job_patch(
            repo,
            job_id,
            job,
            status="running",
            phase="consulting",
            startedAt=now_iso(),
            pid=os.getpid(),
            providerPids={},
        )


def run_worker(repo: Path, job_id: str) -> int:
    job = claim_worker(repo, job_id)
    if job is None:
        return 1
    if job.get("status") == "cancelled":
        return 1
    active: dict[str, subprocess.Popen[str]] = {}
    active_lock = threading.Lock()
    cancel_event = threading.Event()

    def handle_signal(_signum: int, _frame: Any) -> None:
        cancel_event.set()
        with active_lock:
            processes = list(active.values())
        for process in processes:
            terminate_pid(process.pid)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, handle_signal)

    append_log(log_path(repo, job_id), f"started {', '.join(job['providers'])}")
    results: dict[str, Any] = {}
    try:
        spec = load_spec(repo, job_id)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(job["providers"])) as executor:
            futures = {
                executor.submit(
                    run_provider,
                    provider,
                    spec.task,
                    argparse.Namespace(**{**vars(spec), "job_id": job_id}),
                    repo,
                    active,
                    active_lock,
                    cancel_event,
                ): provider
                for provider in job["providers"]
            }
            for future in concurrent.futures.as_completed(futures):
                provider = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - defensive worker boundary
                    result = {"status": "failed", "error": f"worker error: {exc}"}
                results[provider] = result
                update_job(repo, job_id, providerResults=results.copy())

        cancelled = cancel_marker(repo, job_id).exists()
        successful = [
            {"provider": provider, **result}
            for provider, result in results.items()
            if result.get("status") == "completed"
        ]
        status = "cancelled" if cancelled else ("completed" if successful else "failed")
        phase = "cancelled" if cancelled else ("done" if successful else "failed")
        summary = f"{len(successful)}/{len(job['providers'])} consultant(s) completed"
        update_job(
            repo,
            job_id,
            status=status,
            phase=phase,
            summary=summary,
            providerResults=results,
            reports=successful,
            pid=None,
            providerPids={},
            completedAt=now_iso(),
        )
        append_log(log_path(repo, job_id), summary)
        return 0 if status == "completed" else 1
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        cancelled = cancel_marker(repo, job_id).exists()
        update_job(
            repo,
            job_id,
            status="cancelled" if cancelled else "failed",
            phase="cancelled" if cancelled else "failed",
            error=None if cancelled else compact(str(exc)),
            pid=None,
            providerPids={},
            completedAt=now_iso(),
        )
        append_log(log_path(repo, job_id), f"worker failed: {exc}")
        return 1


def cancel_marker(repo: Path, job_id: str) -> Path:
    return jobs_dir(repo) / f"{job_id}.cancel"


def request_cancel(repo: Path, job_id: str) -> tuple[dict[str, Any], int | None, list[int]]:
    """Record cancellation before signalling anything, closing the finalization race."""
    with state_lock(repo):
        job = load_job(repo, job_id)
        if job is None:
            raise RuntimeError(f"job {job_id} was not found")
        if job.get("status") not in ACTIVE_STATUSES:
            raise RuntimeError(f"job {job_id} is already {job.get('status', 'finished')}")
        write_private_marker(cancel_marker(repo, job_id), now_iso() + "\n")
        pid = job.get("pid")
        raw_provider_pids = job.get("providerPids") or {}
        provider_pids = [
            int(value)
            for value in raw_provider_pids.values()
            if str(value).isdigit()
        ] if isinstance(raw_provider_pids, dict) else []
        if job.get("status") == "queued" and not pid:
            updated = apply_job_patch(
                repo,
                job_id,
                job,
                status="cancelled",
                phase="cancelled",
                completedAt=now_iso(),
                pid=None,
                providerPids={},
            )
        else:
            updated = apply_job_patch(
                repo,
                job_id,
                job,
                status="cancelling",
                phase="cancelling",
                cancelRequestedAt=now_iso(),
            )
        return updated, int(pid) if str(pid).isdigit() else None, provider_pids


def reconcile(repo: Path, job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") not in ACTIVE_STATUSES:
        return job
    pid = job.get("pid")
    if pid and pid_alive(pid):
        return job
    if job.get("status") == "queued" and not pid:
        try:
            created = dt.datetime.fromisoformat(job.get("createdAt", ""))
            age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
        except (TypeError, ValueError):
            age = QUEUE_STALE_SECONDS + 1
        if age <= QUEUE_STALE_SECONDS:
            return job
    if job.get("status") == "cancelling" or cancel_marker(repo, job["id"]).exists():
        return update_job(
            repo,
            job["id"],
            status="cancelled",
            phase="cancelled",
            pid=None,
            providerPids={},
            completedAt=now_iso(),
        )
    return update_job(
        repo,
        job["id"],
        status="failed",
        phase="failed",
        error="worker exited before recording a final result",
        pid=None,
        providerPids={},
        completedAt=now_iso(),
    )


def list_jobs(repo: Path, include_all: bool = False) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    current_session = session_id()
    for path in jobs_dir(repo).glob("consult-*.json"):
        job = read_json(path)
        if job is None or not isinstance(job.get("id"), str):
            continue
        if job.get("workspaceRoot") != str(repo):
            continue
        job = reconcile(repo, job)
        if not include_all and current_session and job.get("sessionId") not in {None, current_session}:
            continue
        jobs.append(job)
    return sorted(jobs, key=lambda item: item.get("updatedAt", ""), reverse=True)


def resolve_job(repo: Path, reference: str | None, include_all: bool = False, terminal: bool | None = None) -> dict[str, Any]:
    jobs = list_jobs(repo, include_all=include_all)
    if terminal is True:
        jobs = [job for job in jobs if job.get("status") in TERMINAL_STATUSES]
    elif terminal is False:
        jobs = [job for job in jobs if job.get("status") in ACTIVE_STATUSES]
    if reference:
        exact = [job for job in jobs if job["id"] == reference]
        prefix = exact or [job for job in jobs if job["id"].startswith(reference)]
        if len(prefix) == 1:
            return prefix[0]
        if len(prefix) > 1:
            raise RuntimeError(f"job reference {reference!r} is ambiguous")
        raise RuntimeError(f"no matching job for {reference!r}")
    if jobs:
        return jobs[0]
    kind = "finished" if terminal is True else "active" if terminal is False else ""
    raise RuntimeError(f"no {kind} consultant jobs found".replace("  ", " "))


def elapsed(job: dict[str, Any]) -> str:
    started = job.get("startedAt") or job.get("createdAt")
    try:
        start = dt.datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return "unknown duration"
    end_value = job.get("completedAt")
    try:
        end = dt.datetime.fromisoformat(end_value) if end_value else dt.datetime.now(dt.timezone.utc)
    except (TypeError, ValueError):
        end = dt.datetime.now(dt.timezone.utc)
    seconds = max(0, int((end - start).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds}s" if minutes else f"{seconds}s"


def render_status(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "No consultant jobs found.\n"
    lines = []
    for job in jobs:
        providers = ",".join(job.get("providers", []))
        summary = job.get("summary") or job.get("error") or "in progress"
        lines.append(f"{job['id']}  {job.get('status', 'unknown')}  {providers}  {elapsed(job)}  {summary}")
    return "\n".join(lines) + "\n"


def render_result(job: dict[str, Any]) -> str:
    lines = [f"Consultant job {job['id']} ({job.get('status')})", job.get("summary", "")]
    for item in job.get("reports", []):
        lines.extend(["", f"=== {item.get('provider', 'consultant')} ===", item.get("report", "").rstrip()])
    failures = [
        f"{provider}: {result.get('error', 'failed')}"
        for provider, result in job.get("providerResults", {}).items()
        if result.get("status") != "completed"
    ]
    if failures:
        lines.extend(["", "Unavailable or failed consultants:", *failures])
    if job.get("error"):
        lines.extend(["", f"Worker error: {job['error']}"])
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def spawn_worker(repo: Path, job_id: str) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "worker", "--job-id", job_id],
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **process_group_kwargs(),
    )


def launch_or_run(repo: Path, kind: str, providers: list[str], task: str, options: argparse.Namespace) -> int:
    job_id = create_job(repo, kind, providers, task, options)
    try:
        process = spawn_worker(repo, job_id)
    except OSError as exc:
        update_job(
            repo,
            job_id,
            status="failed",
            phase="failed",
            error=f"could not start worker: {exc}",
            completedAt=now_iso(),
        )
        raise RuntimeError(f"could not start worker: {exc}") from exc

    if options.background and not options.wait:
        if options.json:
            print(json.dumps(load_job(repo, job_id) or {"id": job_id}, indent=2, sort_keys=True))
        else:
            print(f"Queued consultant job {job_id} ({', '.join(providers)}).")
            print(f"Use `codex-consult status {job_id}` and `codex-consult result {job_id}` to follow it.")
        return 0

    try:
        result_code = process.wait()
    except KeyboardInterrupt:
        print(f"Worker continues in the background for job {job_id}; use `codex-consult cancel {job_id}` to stop it.", file=sys.stderr)
        return 130
    job = load_job(repo, job_id) or {}
    if options.json:
        print(json.dumps(job, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_result(job))
    return result_code


def add_execution_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", action="append", help="agy, opencode, or all; repeatable (default: all)")
    parser.add_argument("--phase", choices=("plan", "diff"), default="diff")
    parser.add_argument("--scope", choices=("auto", "working-tree", "branch"), default="auto")
    parser.add_argument("--base", help="branch/ref used for clean branch reviews")
    parser.add_argument("--path", action="append", default=[], help="relevant repository path; repeatable")
    parser.add_argument("--model", action="append", default=[], help="provider model; repeatable")
    parser.add_argument("--max-bytes", type=int, default=80_000)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--variant", help="OpenCode reasoning variant")
    parser.add_argument("--print-timeout", default="120s")
    parser.add_argument("--agent")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("consult", "review", "adversarial-review"):
        child = subparsers.add_parser(command)
        child.add_argument("prompt", nargs="*", default=[])
        add_execution_options(child)

    setup = subparsers.add_parser("setup")
    setup.add_argument("--json", action="store_true")
    setup.add_argument("--cwd", type=Path, default=Path.cwd())

    status = subparsers.add_parser("status")
    status.add_argument("job_id", nargs="?")
    status.add_argument("--all", action="store_true")
    status.add_argument("--json", action="store_true")
    status.add_argument("--cwd", type=Path, default=Path.cwd())

    result = subparsers.add_parser("result")
    result.add_argument("job_id", nargs="?")
    result.add_argument("--json", action="store_true")
    result.add_argument("--cwd", type=Path, default=Path.cwd())

    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("job_id", nargs="?")
    cancel.add_argument("--json", action="store_true")
    cancel.add_argument("--cwd", type=Path, default=Path.cwd())

    worker = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--job-id", required=True)
    worker.add_argument("--cwd", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def setup_report(repo: Path) -> dict[str, Any]:
    return {
        "workspaceRoot": str(repo),
        "stateDirectory": str(state_dir(repo)),
        "python": sys.executable,
        "providers": {
            provider: {"available": shutil.which(command) is not None, "command": command}
            for provider, command in (("agy", "agy"), ("opencode", "opencode"))
        },
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv or sys.argv[1:])
        if args.command == "worker":
            repo = find_repo_root(args.cwd)
            return run_worker(repo, args.job_id)

        repo = find_repo_root(args.cwd)
        if args.command == "setup":
            report = setup_report(repo)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(f"Workspace: {report['workspaceRoot']}")
                print(f"State: {report['stateDirectory']}")
                for provider, value in report["providers"].items():
                    print(f"{provider}: {'available' if value['available'] else 'missing'}")
            return 0

        if args.command == "status":
            if args.job_id:
                jobs = [resolve_job(repo, args.job_id, include_all=True)]
            else:
                jobs = list_jobs(repo, include_all=args.all)
            if args.json:
                print(json.dumps(jobs, indent=2, sort_keys=True))
            else:
                sys.stdout.write(render_status(jobs))
            return 0

        if args.command == "result":
            job = resolve_job(repo, args.job_id, include_all=bool(args.job_id), terminal=True)
            if args.json:
                print(json.dumps(job, indent=2, sort_keys=True))
            else:
                sys.stdout.write(render_result(job))
            return 0 if job.get("status") == "completed" else 1

        if args.command == "cancel":
            if args.job_id:
                job = resolve_job(repo, args.job_id, include_all=True, terminal=False)
            else:
                active_jobs = list_jobs(repo, include_all=False)
                active_jobs = [candidate for candidate in active_jobs if candidate.get("status") in ACTIVE_STATUSES]
                if len(active_jobs) > 1:
                    raise RuntimeError("multiple consultant jobs are active; pass a job id to cancel")
                job = active_jobs[0] if active_jobs else resolve_job(repo, None, terminal=False)
            updated, worker_pid, provider_pids = request_cancel(repo, job["id"])
            targets = [pid for pid in [worker_pid, *provider_pids] if pid]
            delivered = any(terminate_pid(pid) for pid in targets)
            for pid in targets:
                if not wait_for_exit(pid):
                    delivered = terminate_pid(pid, force=True) or delivered
            if updated.get("status") == "cancelling":
                current = load_job(repo, job["id"])
                if current and not pid_alive(current.get("pid")):
                    updated = reconcile(repo, current)
            updated = update_job(repo, job["id"], cancelDelivered=delivered)
            payload = {"job": updated, "signalDelivered": delivered}
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                verb = "Cancelled" if updated.get("status") == "cancelled" else "Cancellation requested for"
                print(f"{verb} consultant job {job['id']}.")
            return 0

        if args.max_bytes <= 0 or args.timeout <= 0 or args.retries < 0 or args.retries > 2:
            raise ValueError("max-bytes and timeout must be positive; retries must be between 0 and 2")
        prompt = " ".join(args.prompt).strip()
        if args.command == "consult" and not prompt:
            raise ValueError("provide a consultation task")
        task = prompt or "Review the current repository for actionable correctness and regression risks."
        if args.command == "review":
            task = prompt_template("review")
        elif args.command == "adversarial-review":
            focus = prompt or "hidden assumptions, failure modes, races, recovery, and security boundaries"
            task = prompt_template("adversarial-review").replace("{focus}", focus)
        target_context = review_target_context(repo, args.scope, args.base, args.max_bytes)
        task = f"{task}\n\nREVIEW TARGET CONTEXT:\n{target_context}"
        providers = provider_names(args.provider, default="all")
        return launch_or_run(repo, args.command, providers, task, args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"codex-consult: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
