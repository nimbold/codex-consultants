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
        assert (codex_home / "skills" / "agy-consultant" / "SKILL.md").is_file()
        assert (launcher_dir / "codex-agy-consult").is_file()
        guidance = (codex_home / "AGENTS.md").read_text(encoding="utf-8")
        assert "codex-agy-consultant:start" in guidance

        refused = run("--codex-home", str(codex_home), "--launcher-dir", str(launcher_dir))
        assert refused.returncode != 0
        assert "--force" in refused.stderr

        replaced = run(
            "--codex-home", str(codex_home),
            "--launcher-dir", str(launcher_dir),
            "--force",
        )
        assert replaced.returncode == 0, replaced.stderr
        assert list((codex_home / "skills").glob("agy-consultant.backup-*"))
        assert list(launcher_dir.glob("codex-agy-consult.backup-*"))

    print("installer smoke test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
