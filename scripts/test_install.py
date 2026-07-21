#!/usr/bin/env python3
"""Smoke-test the manual installer without touching the user's Codex home."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-agy-install-test-") as temp:
        base = Path(temp)
        codex_home = base / "codex"
        launcher_dir = base / "bin"

        dry = run("--codex-home", str(codex_home), "--launcher-dir", str(launcher_dir), "--dry-run")
        assert dry.returncode == 0, dry.stderr
        assert not codex_home.exists()

        installed = run(
            "--codex-home", str(codex_home),
            "--launcher-dir", str(launcher_dir),
            "--install-guidance",
        )
        assert installed.returncode == 0, installed.stderr
        assert (codex_home / "skills" / "agy-consult" / "SKILL.md").is_file()
        assert (codex_home / "skills" / "hermes-consult" / "SKILL.md").is_file()
        assert (codex_home / "skills" / "opencode-consult" / "SKILL.md").is_file()
        assert (launcher_dir / "codex-agy-consult").is_file()
        assert (launcher_dir / "codex-hermes-consult").is_file()
        assert (launcher_dir / "codex-opencode").is_file()
        assert (launcher_dir / "codex-opencode-consult").is_file()
        installed_skill = (codex_home / "skills" / "agy-consult" / "SKILL.md").read_text(encoding="utf-8")
        hermes_skill = (codex_home / "skills" / "hermes-consult" / "SKILL.md").read_text(encoding="utf-8")
        opencode_skill = (codex_home / "skills" / "opencode-consult" / "SKILL.md").read_text(encoding="utf-8")
        assert "Explicit invocation only" in installed_skill
        assert "$agy-consult" in installed_skill
        assert "$hermes-consult" in hermes_skill
        assert "thinkingmachines/inkling" in hermes_skill
        assert "reasoning set to `max`" in hermes_skill
        assert "minimaxai/minimax-m3" in hermes_skill
        assert "$opencode-consult" in opencode_skill
        assert "opencode/deepseek-v4-flash-free" in opencode_skill
        assert "max" in opencode_skill
        guidance = (codex_home / "AGENTS.md").read_text(encoding="utf-8")
        assert "codex-consultants:start" in guidance
        assert "Agy, Hermes, and OpenCode are explicit opt-in second opinions" in guidance
        assert "unless the user explicitly requests" in guidance

        refused = run("--codex-home", str(codex_home), "--launcher-dir", str(launcher_dir))
        assert refused.returncode != 0
        assert "--force" in refused.stderr

        replaced = run(
            "--codex-home", str(codex_home),
            "--launcher-dir", str(launcher_dir),
            "--force",
        )
        assert replaced.returncode == 0, replaced.stderr
        assert list((codex_home / "skill-backups").glob("agy-consult.backup-*"))
        assert list(launcher_dir.glob("codex-agy-consult.backup-*"))
        assert list((codex_home / "skill-backups").glob("hermes-consult.backup-*"))
        assert list(launcher_dir.glob("codex-hermes-consult.backup-*"))
        assert list(launcher_dir.glob("codex-opencode.backup-*"))
        assert list((codex_home / "skill-backups").glob("opencode-consult.backup-*"))
        assert list(launcher_dir.glob("codex-opencode-consult.backup-*"))

    print("installer smoke test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
