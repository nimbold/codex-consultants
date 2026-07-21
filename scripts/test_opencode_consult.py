#!/usr/bin/env python3
"""Smoke-test OpenCode consultation command construction without invoking OpenCode."""

from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "codex-consultants" / "skills" / "opencode-consult" / "scripts" / "opencode_consult.py"


def load_module():
    spec = importlib.util.spec_from_file_location("opencode_consult", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    command_file = ROOT / "plugins" / "codex-consultants" / "commands" / "opencode.md"
    command_text = command_file.read_text(encoding="utf-8")
    assert command_text.startswith("---\n")
    assert "description:" in command_text
    for heading in ("## Preflight", "## Plan", "## Commands", "## Verification", "## Summary", "## Next Steps"):
        assert heading in command_text
    assert "/opencode" in command_text or "OpenCode Consultation" in command_text

    args = Namespace(models=None, variant=None)
    assert module.resolve_models(args) == [module.DEFAULT_MODEL]
    assert module.resolve_variant(module.DEFAULT_MODEL, None) == "max"
    assert module.resolve_variant("opencode/mimo-v2.5-free", None) is None
    assert module.resolve_variant("opencode/mimo-v2.5-free", "high") == "high"
    assert module.FREE_MODELS == (
        "opencode/deepseek-v4-flash-free",
        "opencode/big-pickle",
        "opencode/mimo-v2.5-free",
        "opencode/north-mini-code-free",
        "opencode/nemotron-3-ultra-free",
    )

    workspace = Path("/tmp/opencode-consult-workspace")
    command = module.build_command(
        "/usr/local/bin/opencode",
        module.DEFAULT_MODEL,
        "max",
        workspace,
        "payload",
    )
    assert command == [
        "/usr/local/bin/opencode",
        "run",
        "--model",
        module.DEFAULT_MODEL,
        "--agent",
        module.CONSULTANT_AGENT,
        "--format",
        "default",
        "--pure",
        "--dir",
        str(workspace),
        "--variant",
        "max",
        "payload",
    ]
    assert "--variant" not in module.build_command(
        "opencode", "opencode/mimo-v2.5-free", None, workspace, "payload"
    )

    config = module.build_isolated_config(module.DEFAULT_MODEL)
    assert config["model"] == module.DEFAULT_MODEL
    assert config["permission"]["*"] == "deny"
    assert config["permission"]["read"] == "allow"
    assert "edit" not in config["permission"]
    agent = config["agent"][module.CONSULTANT_AGENT]
    assert agent["mode"] == "primary"
    assert agent["permission"]["*"] == "deny"
    assert "Do not edit" in agent["prompt"]

    with module.isolated_opencode_environment(module.DEFAULT_MODEL) as env:
        config_path = Path(env[module.OPENCODE_CONFIG_ENV])
        assert config_path.is_file()
        assert module.OPENCODE_CONFIG_DIR_ENV not in env
        assert json.loads(config_path.read_text(encoding="utf-8"))["model"] == module.DEFAULT_MODEL

    compact = module.COMMON.compact_report(
        "REPORT: OpenCode completed a bounded review.\n"
        "FINDING: HIGH | FACT | src/main.py:10 | Input is unchecked. | Invalid state reaches the parser. | Worst case is a crash. | High | Add validation."
    )
    assert "REPORT: OpenCode completed a bounded review." in compact
    assert "FINDING: HIGH" in compact

    print("opencode consultation smoke test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
