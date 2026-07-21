#!/usr/bin/env python3
"""Run a bounded, read-only OpenCode CLI consultation for the current Git repo."""

from __future__ import annotations

import argparse
import importlib.util
import json
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
# Free model quotas are provider-managed; do not spend another request unless
# the caller explicitly opts into retries.
DEFAULT_RETRIES = 0
RETRY_DELAY_SECONDS = 2.0
DEFAULT_MODEL = "opencode/deepseek-v4-flash-free"
DEFAULT_VARIANT = "max"
CONSULTANT_AGENT = "codex-consultant"
MAX_MODELS = 3
FREE_MODELS = (
    "opencode/deepseek-v4-flash-free",
    "opencode/big-pickle",
    "opencode/mimo-v2.5-free",
    "opencode/north-mini-code-free",
    "opencode/nemotron-3-ultra-free",
)
OPENCODE_CONFIG_ENV = "OPENCODE_CONFIG"
OPENCODE_CONFIG_DIR_ENV = "OPENCODE_CONFIG_DIR"


def load_bundle_helpers():
    """Reuse the existing bounded bundle and report contract."""
    spec = importlib.util.spec_from_file_location("codex_consultant_bundle", AGY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load shared bundle helpers from {AGY_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMON = load_bundle_helpers()


def fail(message: str, code: int = 2) -> int:
    print(f"codex-opencode-consult: {message}", file=sys.stderr)
    return code


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


def resolve_variant(model: str, requested: str | None) -> str | None:
    """Use DeepSeek's max variant by default without breaking other free models."""
    if requested is not None:
        variant = requested.strip()
        if not variant:
            raise ValueError("--variant must not be empty")
        return variant
    return DEFAULT_VARIANT if model.strip().lower() == DEFAULT_MODEL else None


def build_command(
    opencode: str,
    model: str,
    variant: str | None,
    workspace: Path,
    payload: str,
) -> list[str]:
    """Build a non-interactive command that stays inside the isolated workspace."""
    command = [
        opencode,
        "run",
        "--model",
        model,
        "--agent",
        CONSULTANT_AGENT,
        "--format",
        "default",
        "--pure",
        "--dir",
        str(workspace),
    ]
    if variant is not None:
        command.extend(["--variant", variant])
    command.append(payload)
    return command


def build_isolated_config(model: str) -> dict:
    """Create a config that makes the OpenCode consultation read-only."""
    read_only_permissions = {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "external_directory": "deny",
    }
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": model,
        "permission": read_only_permissions,
        "agent": {
            CONSULTANT_AGENT: {
                "mode": "primary",
                "model": model,
                "permission": read_only_permissions,
                "prompt": (
                    "You are a read-only code-review consultant. Do not edit, write, patch, "
                    "delete, execute commands, call subagents, access the network, or ask the "
                    "user questions. Return only the compact report format requested by the "
                    "consultation payload."
                ),
            }
        },
    }


@contextmanager
def isolated_opencode_environment(model: str):
    """Expose only a temporary read-only OpenCode config to the child process."""
    with tempfile.TemporaryDirectory(prefix="codex-opencode-config-") as isolated_config:
        config_path = Path(isolated_config) / "opencode.json"
        config_path.write_text(
            json.dumps(build_isolated_config(model), indent=2) + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env[OPENCODE_CONFIG_ENV] = str(config_path)
        # Do not let a user-selected config directory add plugins, commands, or
        # agent overrides to this bounded consultation.
        env.pop(OPENCODE_CONFIG_DIR_ENV, None)
        yield env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="task context; stdin is used when omitted")
    parser.add_argument("--phase", choices=("plan", "diff"), default="diff")
    parser.add_argument("--path", action="append", default=[], help="relevant repository file to include; repeatable")
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        help=(
            f"OpenCode model id in provider/model form; repeat for independent opinions "
            f"(default: {DEFAULT_MODEL}; max: {MAX_MODELS})"
        ),
    )
    parser.add_argument(
        "--variant",
        help=f"provider-specific reasoning variant; default is {DEFAULT_VARIANT!r} for the DeepSeek free model",
    )
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"retry transient OpenCode failures (default: {DEFAULT_RETRIES}; max: 2)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_bytes <= 0 or args.timeout <= 0:
        return fail("--max-bytes and --timeout must be positive")
    if args.retries < 0 or args.retries > 2:
        return fail("--retries must be between 0 and 2")
    try:
        models = resolve_models(args)
        variants = {model: resolve_variant(model, args.variant) for model in models}
    except ValueError as exc:
        return fail(str(exc))

    task = args.prompt if args.prompt is not None else sys.stdin.read()
    if not task.strip():
        return fail("provide a consultation task as the positional prompt or on stdin")

    opencode = shutil.which("opencode")
    if not opencode:
        return fail("opencode was not found on PATH")

    try:
        repo = COMMON.find_repo_root()
        payload, selected = COMMON.build_payload(repo, args.phase, task, args.max_bytes, args.path)
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc))

    responses = []
    unavailable = []
    for model in models:
        variant = variants[model]
        deadline = time.monotonic() + args.timeout
        model_failure = None
        for attempt in range(args.retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                model_failure = f"timed out after {args.timeout} seconds"
                break
            try:
                with tempfile.TemporaryDirectory(prefix="codex-opencode-consult-") as isolated_cwd:
                    workspace = Path(isolated_cwd)
                    COMMON.materialize_selected_files(workspace, selected)
                    command = build_command(opencode, model, variant, workspace, payload)
                    with isolated_opencode_environment(model) as opencode_env:
                        result = subprocess.run(
                            command,
                            cwd=workspace,
                            text=True,
                            capture_output=True,
                            timeout=remaining,
                            check=False,
                            env=opencode_env,
                        )
            except subprocess.TimeoutExpired:
                model_failure = f"timed out after {args.timeout} seconds"
            except OSError as exc:
                model_failure = f"could not start opencode: {exc}"
            else:
                if result.returncode != 0:
                    detail = COMMON.compact_diagnostic(result.stderr) or "opencode returned no diagnostic"
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
        return fail(f"all OpenCode consultations unavailable: {detail}", 4)

    if len(responses) == 1:
        _, stdout, stderr = responses[0]
        sys.stdout.write(stdout)
        if stderr:
            print(stderr, file=sys.stderr)
    else:
        for model, stdout, stderr in responses:
            print(f"=== OpenCode consultation: {model} ===")
            sys.stdout.write(stdout)
            if not stdout.endswith("\n"):
                print()
            if stderr:
                print(f"[{model}] {stderr}", file=sys.stderr)

    if unavailable:
        print(
            "codex-opencode-consult: unavailable model(s): " + "; ".join(unavailable),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
