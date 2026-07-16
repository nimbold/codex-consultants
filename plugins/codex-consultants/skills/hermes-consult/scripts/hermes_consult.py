#!/usr/bin/env python3
"""Run a bounded, read-only Hermes consultation for the current Git repo."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
AGY_SCRIPT = PLUGIN_ROOT / "skills" / "agy-consult" / "scripts" / "agy_consult.py"
DEFAULT_MAX_BYTES = 80_000
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_RETRIES = 1
RETRY_DELAY_SECONDS = 2.0
DEFAULT_PROVIDER = "nvidia"
DEFAULT_MODEL = "minimaxai/minimax-m3"
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


def build_command(hermes: str, args: argparse.Namespace, payload: str, model: str) -> list[str]:
    """Build a safe one-shot command without touching the real worktree."""
    return [
        hermes,
        "--oneshot",
        "--provider",
        args.provider,
        "--model",
        model,
        "--safe-mode",
        "--ignore-rules",
        "--ignore-user-config",
        payload,
    ]


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
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"retry transient Hermes failures (default: {DEFAULT_RETRIES}; max: 2)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_bytes <= 0 or args.timeout <= 0:
        return fail("--max-bytes and --timeout must be positive")
    if args.retries < 0 or args.retries > 2:
        return fail("--retries must be between 0 and 2")
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
        deadline = time.monotonic() + args.timeout
        model_failure = None
        for attempt in range(args.retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                model_failure = f"timed out after {args.timeout} seconds"
                break
            try:
                with tempfile.TemporaryDirectory(prefix="codex-hermes-consult-") as isolated_cwd:
                    COMMON.materialize_selected_files(Path(isolated_cwd), selected)
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
