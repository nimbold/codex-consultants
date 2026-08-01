#!/usr/bin/env python3
"""Smoke-test the shared consultant control plane without provider clients."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from argparse import Namespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "codex-consultants" / "skills" / "codex-consult" / "scripts" / "consultant_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("consultant_runtime", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def options(**overrides):
    values = {
        "phase": "diff",
        "scope": "auto",
        "base": None,
        "max_bytes": 80_000,
        "timeout": 30,
        "retries": 0,
        "model": [],
        "path": [],
        "variant": None,
        "print_timeout": "120s",
        "agent": None,
        "job_id": None,
        "background": False,
        "wait": False,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def main() -> int:
    module = load_module()
    assert module.provider_names(None) == ["agy", "opencode"]
    assert module.provider_names(["agy", "opencode", "agy"]) == ["agy", "opencode"]
    assert module.provider_names(["agy,opencode"]) == ["agy", "opencode"]
    assert module.prompt_template("review").startswith("Perform a normal")
    assert "adversarial" in module.prompt_template("adversarial-review").lower()
    assert ("start_new_session" in module.process_group_kwargs()) == (os.name != "nt")
    diagnostic = module.compact("token=abc123 Authorization: Bearer sk_test_123")
    assert "token=[redacted]" in diagnostic and "[redacted-token]" in diagnostic

    with tempfile.TemporaryDirectory(prefix="codex-consult-runtime-test-") as temp:
        root = Path(temp).resolve()
        state = root / "state"
        os.environ[module.STATE_ENV] = str(state)
        fake = root / "fake_provider.py"
        fake.write_text(
            "import sys\n"
            "if 'fail' in sys.argv:\n"
            "    print('synthetic failure', file=sys.stderr)\n"
            "    raise SystemExit(7)\n"
            "print('REPORT: synthetic provider completed')\n"
            "print('FINDING: LOW | FACT | test.py:1 | synthetic evidence | no impact | normal only | high | verify')\n",
            encoding="utf-8",
        )
        module.PROVIDER_SCRIPTS = {provider: fake for provider in module.PROVIDER_ORDER}

        review_repo = root / "review-repo"
        review_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=review_repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=review_repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=review_repo, check=True)
        (review_repo / "source.py").write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "add", "source.py"], cwd=review_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=review_repo, check=True)
        (review_repo / "source.py").write_text("after\n", encoding="utf-8")
        subprocess.run(["git", "add", "source.py"], cwd=review_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=review_repo, check=True)
        branch_context = module.review_target_context(review_repo, "branch", "HEAD~1")
        assert "Review target: branch against HEAD~1" in branch_context
        assert "source.py" in branch_context
        assert "after" in branch_context
        assert len(module.review_target_context(review_repo, "working-tree", None, max_bytes=128).encode()) <= 128

        job_options = options()
        job_id = module.create_job(root, "review", ["agy", "opencode"], "synthetic review", job_options)
        record = module.load_job(root, job_id)
        assert record is not None and record["status"] == "queued"
        assert module.load_spec(root, job_id).task == "synthetic review"
        assert stat.S_IMODE(module.job_path(root, job_id).stat().st_mode) == 0o600
        assert stat.S_IMODE(module.log_path(root, job_id).stat().st_mode) == 0o600

        assert module.run_worker(root, job_id) == 0
        finished = module.load_job(root, job_id)
        assert finished is not None and finished["status"] == "completed"
        assert len(finished["reports"]) == 2
        assert "synthetic provider completed" in module.render_result(finished)
        assert module.resolve_job(root, job_id, terminal=True)["id"] == job_id

        stale_id = module.create_job(root, "review", ["agy"], "stale", job_options)
        module.update_job(root, stale_id, status="running", pid=999_999, startedAt=module.now_iso())
        stale = module.list_jobs(root, include_all=True)
        stale_record = next(job for job in stale if job["id"] == stale_id)
        assert stale_record["status"] == "failed"
        assert "worker exited" in stale_record["error"]

        failing_id = module.create_job(root, "review", ["opencode"], "fail", job_options)
        assert module.run_worker(root, failing_id) == 1
        failed = module.load_job(root, failing_id)
        assert failed is not None and failed["status"] == "failed"
        assert "synthetic failure" in module.render_result(failed)

        queued_cancel_id = module.create_job(root, "review", ["agy"], "queued cancellation", job_options)
        cancelled, worker_pid, provider_pids = module.request_cancel(root, queued_cancel_id)
        assert cancelled["status"] == "cancelled"
        assert worker_pid is None and provider_pids == []
        assert module.load_job(root, queued_cancel_id)["status"] == "cancelled"

        completed_cancel_id = module.create_job(root, "review", ["agy"], "completed cancellation", job_options)
        assert module.run_worker(root, completed_cancel_id) == 0
        try:
            module.request_cancel(root, completed_cancel_id)
        except RuntimeError as exc:
            assert "already completed" in str(exc)
        else:
            raise AssertionError("completed job was cancellable after finalization")

        stale_queue_id = module.create_job(root, "review", ["agy"], "stale queue", job_options)
        old_created = (module.dt.datetime.now(module.dt.timezone.utc) - module.dt.timedelta(seconds=module.QUEUE_STALE_SECONDS + 1)).isoformat()
        module.update_job(root, stale_queue_id, createdAt=old_created)
        stale_queue = next(job for job in module.list_jobs(root, include_all=True) if job["id"] == stale_queue_id)
        assert stale_queue["status"] == "failed"
        assert "worker exited" in stale_queue["error"]

        other_repo = root / "other-repo"
        other_repo.mkdir()
        foreign_id = module.create_job(other_repo, "review", ["agy"], "foreign workspace", job_options)
        assert all(job["id"] != foreign_id for job in module.list_jobs(root, include_all=True))

        status = module.render_status(module.list_jobs(root, include_all=True))
        assert job_id in status and stale_id in status and failing_id in status and queued_cancel_id in status
        assert json.loads(module.job_path(root, job_id).read_text(encoding="utf-8"))["id"] == job_id

        del os.environ[module.STATE_ENV]

    print("runtime control-plane smoke test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
