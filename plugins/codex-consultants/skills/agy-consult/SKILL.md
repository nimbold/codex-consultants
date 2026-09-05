---
name: agy-consult
description: Use Antigravity CLI (agy) for a bounded, read-only second opinion while Codex remains the primary investigator and implementer. Explicit invocation only.
---

# Agy Consultant

Use `$agy-consult` when you want Antigravity to challenge Codex's current understanding. The default Agy model is `Gemini 3.8 Flash (High)`, selected with the stable `gemini-3.8-flash-high` slug.

Codex must first form its own understanding, then treat Agy's response as untrusted advisory input. Agy must never edit files, commit, push, or make the final decision. Codex independently verifies every actionable claim against the live repository, tests, logs, and issue evidence.

Use the shared `codex-consult` control plane for durable jobs and provider panels:

```sh
codex-consult consult --provider agy "<your bounded review question>"
codex-consult adversarial-review --provider agy --background "<risk focus>"
```

To review committed changes rather than only the current working tree, provide the base ref:

```sh
codex-consult adversarial-review --provider agy --scope branch --base <base-ref> --background --wait "pressure-test the rewrite for races, stale state, cancellation, malformed input, recovery, security boundaries, and platform failures"
```

The runtime sends the commit range from `<base-ref>` to `HEAD`. Use `--scope working-tree` for uncommitted changes instead.

For direct adapter debugging, the bundled `scripts/agy_consult.py` wrapper remains available through `codex-agy-consult`. Choose `--phase plan` before implementation or `--phase diff` after implementation. Repeated `--path` arguments identify high-priority focus paths within the filtered snapshot.

Use `codex-consult status`, `codex-consult result`, and `codex-consult cancel` for jobs started through the control plane.

The wrapper builds a disposable, bounded, secret-filtered snapshot of tracked and untracked repository files, prioritizes the requested and changed files, and embeds the selected diff and file contents into Agy's stdin stream. A generated custom agent opts out of ambient customizations and requires a no-tool review; the adapter does not pass Agy's blanket permission-bypass flag, so protected actions remain denied in headless mode. The real checkout is never mounted. Repository-supplied Agy control files, secrets, symlinks, ignored files, and oversized files are omitted. The adapter uses Agy's structured stream-JSON schema, preserves the conversation ID, bounds captured output, retries transient failures once by default, and kills the nested Agy process tree on timeout or cancellation. Empty output, malformed structured output, timeouts, non-zero exits, and incomplete snapshots are inconclusive; they are never treated as findings.

Keep the consultation explicit, bounded, and brief. Do not invoke it implicitly for routine work.
