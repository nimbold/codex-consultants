#!/usr/bin/env python3
"""Run a bounded, read-only Hermes consultation for the current Git repo."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
AGY_SCRIPT = PLUGIN_ROOT / "skills" / "agy-consult" / "scripts" / "agy_consult.py"
DEFAULT_MAX_BYTES = 80_000
DEFAULT_TIMEOUT_SECONDS = 300
# NVIDIA free-tier quotas are request-based; do not spend a second request on
# a transient failure unless the caller explicitly opts into retries.
DEFAULT_RETRIES = 0
RETRY_DELAY_SECONDS = 2.0
DEFAULT_PROVIDER = "nvidia"
DEFAULT_MODEL = "thinkingmachines/inkling"
DEFAULT_REASONING_EFFORT = "max"
DEFAULT_THINKING_MODE = "enabled"
THINKING_MODES = ("enabled", "disabled", "adaptive")
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
NVIDIA_BASE_URL = "${NVIDIA_BASE_URL}"
NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
ISOLATED_PROVIDER = "codex-consultants-nvidia"
DEFAULT_RPM_LIMIT = 39
RATE_WINDOW_SECONDS = 60.0
RATE_WINDOW_EPSILON_SECONDS = 0.01
RATE_STATE_ENV = "CODEX_HERMES_RATE_STATE"
DEFAULT_RATE_STATE_PATH = Path.home() / ".codex" / "state" / "codex-consultants-hermes-rpm.json"
MAX_MODELS = 2


def load_bundle_helpers():
    """Reuse the client's bounded bundle and report contract."""
    spec = importlib.util.spec_from_file_location("codex_consultant_bundle", AGY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load shared bundle helpers from {AGY_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = load_bundle_helpers()


def fail(message: str, code: int = 2) -> int:
    print(f"codex-hermes-consult: {message}", file=sys.stderr)
    return code


def is_minimax_m3(model: str) -> bool:
    """Return True for the MiniMax M3 model id used by NVIDIA NIM."""
    normalized = model.strip().lower()
    return normalized == "minimax-m3" or normalized.endswith("/minimax-m3")


def is_inkling(model: str) -> bool:
    """Return True for Thinking Machines' Inkling model id."""
    normalized = model.strip().lower()
    return normalized == "inkling" or normalized.endswith("/inkling")


def is_glm_5_2(model: str) -> bool:
    """Return True for the GLM 5.2 model id exposed by NVIDIA NIM."""
    normalized = model.strip().lower()
    return normalized == "glm-5.2" or normalized.endswith("/glm-5.2")


def resolve_nvidia_reasoning_effort(model: str, effort: str) -> str | None:
    """Map Hermes effort levels to model-specific NVIDIA wire values.

    Inkling accepts the full documented ``none`` through ``max`` vocabulary.
    GLM 5.2 exposes a simpler native high/max mapping. Unknown NVIDIA models
    are left untouched so this wrapper does not send an unsupported field.
    """
    normalized = (effort or DEFAULT_REASONING_EFFORT).strip().lower()
    if is_inkling(model):
        return "max" if normalized == "ultra" else normalized
    if is_glm_5_2(model):
        if normalized == "none":
            return "none"
        return "max" if normalized in {"xhigh", "max", "ultra"} else "high"
    return None


def resolve_thinking_mode(args: argparse.Namespace) -> str:
    """Map Hermes' effort vocabulary onto MiniMax M3's three wire modes."""
    explicit = getattr(args, "thinking_mode", None)
    if explicit:
        return explicit
    effort = (getattr(args, "reasoning_effort", DEFAULT_REASONING_EFFORT) or "").strip().lower()
    return "disabled" if effort == "none" else DEFAULT_THINKING_MODE


def uses_isolated_nvidia_route(args: argparse.Namespace, model: str) -> bool:
    return args.provider.strip().lower() == DEFAULT_PROVIDER


def build_isolated_config(
    model: str,
    thinking_mode: str,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
) -> dict:
    """Build the only Hermes config visible to an isolated NVIDIA invocation."""
    provider_config = {
        "api": NVIDIA_BASE_URL,
        "key_env": "NVIDIA_API_KEY",
        "default_model": model,
        "transport": "chat_completions",
    }
    if is_minimax_m3(model):
        provider_config["extra_body"] = {
            "chat_template_kwargs": {
                "thinking_mode": thinking_mode,
            },
        }
    agent_config = {
        "max_turns": 1,
        "api_max_retries": 0,
    }
    wire_effort = resolve_nvidia_reasoning_effort(model, reasoning_effort)
    if wire_effort is not None:
        # Hermes' custom OpenAI-compatible provider emits this as the
        # top-level reasoning_effort request field.
        agent_config["reasoning_effort"] = wire_effort
    return {
        "model": {
            "default": model,
            "provider": f"custom:{ISOLATED_PROVIDER}",
        },
        # One Hermes turn and one app-level attempt make the wrapper's
        # per-invocation request count bounded for the RPM limiter below.
        "agent": agent_config,
        "providers": {
            ISOLATED_PROVIDER: provider_config,
        },
    }


def build_command(hermes: str, args: argparse.Namespace, payload: str, model: str) -> list[str]:
    """Build a safe one-shot command without touching the real worktree."""
    if uses_isolated_nvidia_route(args, model):
        provider = f"custom:{ISOLATED_PROVIDER}"
        isolation_flags = ["--ignore-rules", "--toolsets", "file,terminal"]
    else:
        provider = args.provider
        isolation_flags = ["--safe-mode", "--ignore-rules", "--ignore-user-config"]
    return [
        hermes,
        "--oneshot",
        payload,
        "--provider",
        provider,
        "--model",
        model,
        *isolation_flags,
    ]


def configured_hermes_home() -> Path:
    """Locate the user's existing Hermes home without reading credentials."""
    configured = os.environ.get("HERMES_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".hermes"


def configured_rate_state_path() -> Path:
    configured = os.environ.get(RATE_STATE_ENV, "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_RATE_STATE_PATH


def rate_key(args: argparse.Namespace, model: str) -> str:
    return f"{args.provider.strip().lower()}:{model.strip().lower()}"


def recent_rate_slots(timestamps: list[float], now: float) -> list[float]:
    cutoff = now - RATE_WINDOW_SECONDS
    recent = []
    for raw_timestamp in timestamps:
        try:
            timestamp = float(raw_timestamp)
        except (TypeError, ValueError):
            continue
        if math.isfinite(timestamp) and timestamp >= cutoff:
            recent.append(timestamp)
    return sorted(recent)


def rate_wait_seconds(timestamps: list[float], now: float, rpm_limit: int = DEFAULT_RPM_LIMIT) -> float:
    recent = recent_rate_slots(timestamps, now)
    if len(recent) < rpm_limit:
        return 0.0
    return max(0.0, recent[-rpm_limit] + RATE_WINDOW_SECONDS + RATE_WINDOW_EPSILON_SECONDS - now)


def _read_rate_state(path: Path) -> dict[str, list[float]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    models = raw.get("models") if isinstance(raw, dict) else None
    return models if isinstance(models, dict) else {}


def _write_rate_state(path: Path, models: dict[str, list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "models": models}, stream, separators=(",", ":"))
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


@contextmanager
def _rate_state_lock(path: Path):
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - macOS/Linux provide fcntl
        raise RuntimeError("the Hermes RPM limiter requires a platform file-locking primitive") from exc

    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def acquire_rate_slot(
    key: str,
    rpm_limit: int = DEFAULT_RPM_LIMIT,
    state_path: Path | None = None,
    *,
    announce: bool = True,
) -> None:
    """Reserve one request slot, shared by concurrent wrapper processes."""
    path = state_path or configured_rate_state_path()
    while True:
        now = time.time()
        with _rate_state_lock(path):
            models = _read_rate_state(path)
            for model_key, timestamps in list(models.items()):
                if isinstance(timestamps, list):
                    models[model_key] = recent_rate_slots(timestamps, now)
                else:
                    models.pop(model_key, None)

            slots = models.get(key, [])
            wait = rate_wait_seconds(slots, now, rpm_limit)
            if wait <= 0:
                models[key] = recent_rate_slots(slots, now) + [now]
                _write_rate_state(path, models)
                return
            _write_rate_state(path, models)

        if announce:
            print(
                f"codex-hermes-consult: NVIDIA rate limit reached for {key}; waiting {wait:.1f}s",
                file=sys.stderr,
                flush=True,
            )
        time.sleep(min(wait, RATE_WINDOW_SECONDS))


@contextmanager
def isolated_nvidia_environment(model: str, thinking_mode: str, reasoning_effort: str):
    """Yield an isolated Hermes environment for an NVIDIA consultation.

    The temporary profile contains only the provider override needed for the
    NVIDIA request. Model-specific reasoning fields are added when supported;
    MiniMax's thinking field is added separately. The
    user's .env is exposed as a read-only symlink so Hermes can load the
    existing credential without copying it into the temporary workspace or
    placing it in the consultation payload.
    """
    with tempfile.TemporaryDirectory(prefix="codex-hermes-home-") as isolated_home:
        isolated_path = Path(isolated_home)
        (isolated_path / "config.yaml").write_text(
            json.dumps(build_isolated_config(model, thinking_mode, reasoning_effort), indent=2) + "\n",
            encoding="utf-8",
        )

        source_env = configured_hermes_home() / ".env"
        if source_env.is_file():
            try:
                (isolated_path / ".env").symlink_to(source_env)
            except OSError:
                # Do not copy credentials. The child can still use an exported
                # NVIDIA_API_KEY, if one is available in its environment.
                pass

        env = os.environ.copy()
        env["HERMES_HOME"] = str(isolated_path)
        env.pop("HERMES_PROFILE", None)
        env.setdefault("NVIDIA_BASE_URL", NVIDIA_DEFAULT_BASE_URL)
        yield env


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
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=f"Hermes provider (default: {DEFAULT_PROVIDER})",
    )
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        help=f"Hermes model id; repeat for independent opinions (default: {DEFAULT_MODEL}; max: {MAX_MODELS})",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=DEFAULT_REASONING_EFFORT,
        help="Hermes reasoning level; Inkling uses the full none-to-max scale, GLM 5.2 maps to high/max, and MiniMax M3 maps to thinking_mode (default: max)",
    )
    parser.add_argument(
        "--thinking-mode",
        choices=THINKING_MODES,
        help="MiniMax M3 wire mode; overrides --reasoning-effort (enabled, disabled, or adaptive)",
    )
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"retry transient Hermes failures (default: {DEFAULT_RETRIES}; max: 2)",
    )
    parser.add_argument(
        "--rpm-limit",
        type=int,
        default=DEFAULT_RPM_LIMIT,
        help=f"per-model NVIDIA requests per rolling minute (default: {DEFAULT_RPM_LIMIT}; max: {DEFAULT_RPM_LIMIT})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_bytes <= 0 or args.timeout <= 0:
        return fail("--max-bytes and --timeout must be positive")
    if args.retries < 0 or args.retries > 2:
        return fail("--retries must be between 0 and 2")
    if args.rpm_limit < 1 or args.rpm_limit > DEFAULT_RPM_LIMIT:
        return fail(f"--rpm-limit must be between 1 and {DEFAULT_RPM_LIMIT}")
    if not args.provider.strip():
        return fail("--provider must not be empty")
    try:
        models = resolve_models(args)
    except ValueError as exc:
        return fail(str(exc))

    task = args.prompt if args.prompt is not None else sys.stdin.read()
    if not task.strip():
        return fail("provide a consultation task as the positional prompt or on stdin")

    hermes = shutil.which("hermes")
    if not hermes:
        return fail("hermes was not found on PATH")

    try:
        repo = COMMON.find_repo_root()
        payload, selected = COMMON.build_payload(repo, args.phase, task, args.max_bytes, args.path)
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc))

    responses = []
    unavailable = []
    for model in models:
        command = build_command(hermes, args, payload, model)
        thinking_mode = resolve_thinking_mode(args)
        deadline = None
        model_failure = None
        for attempt in range(args.retries + 1):
            if uses_isolated_nvidia_route(args, model):
                try:
                    acquire_rate_slot(rate_key(args, model), args.rpm_limit)
                except (OSError, RuntimeError) as exc:
                    model_failure = f"rate limiter unavailable: {exc}"
                    break
            if deadline is None:
                deadline = time.monotonic() + args.timeout
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                model_failure = f"timed out after {args.timeout} seconds"
                break
            try:
                with tempfile.TemporaryDirectory(prefix="codex-hermes-consult-") as isolated_cwd:
                    COMMON.materialize_selected_files(Path(isolated_cwd), selected)
                    if uses_isolated_nvidia_route(args, model):
                        with isolated_nvidia_environment(model, thinking_mode, args.reasoning_effort) as hermes_env:
                            result = subprocess.run(
                                command,
                                cwd=isolated_cwd,
                                text=True,
                                capture_output=True,
                                timeout=remaining,
                                check=False,
                                env=hermes_env,
                            )
                    else:
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
                model_failure = f"could not start hermes: {exc}"
            else:
                if result.returncode != 0:
                    detail = COMMON.compact_diagnostic(result.stderr) or "hermes returned no diagnostic"
                    model_failure = f"exited with status {result.returncode}: {detail}"
                elif not result.stdout.strip():
                    detail = COMMON.compact_diagnostic(result.stderr)
                    suffix = f" Diagnostic: {detail}" if detail else ""
                    model_failure = f"returned an empty consultation response.{suffix}"
                else:
                    responses.append((model, COMMON.compact_report(result.stdout), COMMON.compact_diagnostic(result.stderr)))
                    model_failure = None
                    break

            if attempt < args.retries:
                time.sleep(min(RETRY_DELAY_SECONDS, max(0.0, deadline - time.monotonic())))

        if model_failure:
            unavailable.append(f"{model}: {model_failure}")

    if not responses:
        detail = "; ".join(unavailable) or "no response"
        return fail(f"all Hermes consultations unavailable: {detail}", 4)

    if len(responses) == 1:
        _, stdout, stderr = responses[0]
        sys.stdout.write(stdout)
        if stderr:
            print(stderr, file=sys.stderr)
    else:
        for model, stdout, stderr in responses:
            print(f"=== Hermes consultation: {model} ===")
            sys.stdout.write(stdout)
            if not stdout.endswith("\n"):
                print()
            if stderr:
                print(f"[{model}] {stderr}", file=sys.stderr)

    if unavailable:
        print(
            "codex-hermes-consult: unavailable model(s): " + "; ".join(unavailable),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
