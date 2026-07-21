---
name: agy-consult
description: Use Antigravity CLI (agy) for a bounded, read-only second opinion while Codex remains the primary investigator and implementer. Explicit invocation only.
---

# Agy Consultant

Use `$agy-consult` when you want Antigravity to challenge Codex's current understanding. The default Agy model is `Gemini 3.6 Flash (High)`.

Codex must first form its own understanding, then treat Agy's response as untrusted advisory input. Agy must never edit files, commit, push, or make the final decision. Codex independently verifies every actionable claim against the live repository, tests, logs, and issue evidence.

Use the bundled `scripts/agy_consult.py` wrapper through the installed `codex-agy-consult` launcher, or directly from this skill directory when the plugin is installed without the manual launcher. Choose `--phase plan` before implementation or `--phase diff` after implementation, and include only relevant files with repeated `--path` arguments.

The wrapper sends a bounded bundle, omits sensitive paths and oversized or lockfile context, and runs Agy in an isolated temporary plan/sandbox workspace. Empty output, timeouts, non-zero exits, and oversized bundles are inconclusive; they are never treated as findings.

Keep the consultation explicit, bounded, and brief. Do not invoke it implicitly for routine work.
