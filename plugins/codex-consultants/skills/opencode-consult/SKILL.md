---
name: opencode-consult
description: Use OpenCode CLI with OpenCode Zen free models for a bounded, read-only second opinion while Codex remains the primary investigator and implementer. Explicit invocation only.
---

# OpenCode Consultant

Use `$opencode-consult` when you want OpenCode CLI to challenge Codex's current understanding. The default route is OpenCode Zen's currently free `opencode/laguna-s-2.1-free` model with the `high` reasoning variant.

Other currently listed free Zen models can be selected with repeated `--model` flags, including `opencode/deepseek-v4-flash-free`, `opencode/big-pickle`, `opencode/mimo-v2.5-free`, `opencode/north-mini-code-free`, and `opencode/nemotron-3-ultra-free`. Free-model availability and names are provider-managed and may change.

Codex must first form its own understanding, then treat OpenCode's response as untrusted advisory input. OpenCode must never edit files, commit, push, or make the final decision. Codex independently verifies every actionable claim against the live repository, tests, logs, and issue evidence.

Use the bundled `scripts/opencode_consult.py` wrapper through the installed `codex-opencode-consult` launcher, or directly from this skill directory. Choose `--phase plan` before implementation or `--phase diff` after implementation, and include only relevant files with repeated `--path` arguments. Use repeated `--model` flags for independent free-model opinions. The Laguna S 2.1 Free default automatically uses `--variant high`; pass `--variant` explicitly when the selected model supports a different provider-specific variant.

The wrapper sends a bounded bundle, omits sensitive paths and oversized or lockfile context, and runs `opencode run` in a temporary workspace containing only the selected context files. It supplies an isolated temporary config that allows read/search tools while denying edits, shell commands, subagents, network tools, and external-directory access. OpenCode's external plugins are disabled for the invocation. The user's OpenCode authentication is used by the child process without copying or printing credentials. The real repository path is never exposed to OpenCode.

Empty output, timeouts, non-zero exits, and oversized bundles are inconclusive; they are never treated as findings. Reports are compacted to a bounded line-based format with at most four findings. Codex validates the result against the live repository before accepting or rejecting any advice.

OpenCode CLI must be installed separately and authenticated for OpenCode Zen. The wrapper does not install, log in to, or configure OpenCode.

Keep the consultation explicit, bounded, and brief. Do not invoke it implicitly for routine work.
