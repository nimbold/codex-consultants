---
name: opencode-consult
description: Use OpenCode CLI models for a bounded, read-only second opinion while Codex remains the primary investigator and implementer. Explicit invocation only.
---

# OpenCode Consultant

Use `$opencode-consult` when you want OpenCode CLI to challenge Codex's current understanding. The default route is NVIDIA's `nvidia/thinkingmachines/inkling` model (`Inkling`). OpenCode's current catalog reports reasoning support for Inkling, but exposes no selectable reasoning variant, so the wrapper leaves that provider-native behavior unchanged.

Other currently listed model ids can be selected with repeated `--model` flags, including `opencode/deepseek-v4-flash-free`, `opencode/big-pickle`, `opencode/mimo-v2.5-free`, `opencode/north-mini-code-free`, and `opencode/nemotron-3-ultra-free`. Provider model availability and names are managed externally and may change.

Codex must first form its own understanding, then treat OpenCode's response as untrusted advisory input. OpenCode must never edit files, commit, push, or make the final decision. Codex independently verifies every actionable claim against the live repository, tests, logs, and issue evidence.

Use the shared `codex-consult` control plane for durable jobs and provider panels:

```sh
codex-consult consult --provider opencode "<your bounded review question>"
codex-consult adversarial-review --provider opencode --background "<risk focus>"
```

For direct adapter debugging, the bundled `scripts/opencode_consult.py` wrapper remains available through `codex-opencode-consult`. Choose `--phase plan` before implementation or `--phase diff` after implementation, and include only relevant files with repeated `--path` arguments. Use repeated `--model` flags for independent model opinions. The Inkling default does not pass `--variant` because its catalog entry has no selectable variants; pass `--variant` explicitly when another selected model supports one.

Use `codex-consult status`, `codex-consult result`, and `codex-consult cancel` for jobs started through the control plane.

The wrapper sends a bounded bundle, omits sensitive paths and oversized or lockfile context, and runs `opencode run` in a temporary workspace containing only the selected context files. It supplies an isolated temporary config that allows read/search tools while denying edits, shell commands, subagents, network tools, and external-directory access. OpenCode's external plugins are disabled for the invocation. The user's OpenCode authentication is used by the child process without copying or printing credentials. The real repository path is never exposed to OpenCode.

Empty output, timeouts, non-zero exits, and oversized bundles are inconclusive; they are never treated as findings. Reports are compacted to a bounded line-based format with at most four findings. Codex validates the result against the live repository before accepting or rejecting any advice.

OpenCode CLI must be installed separately and authenticated for the provider selected by the model id; the default requires the NVIDIA provider credentials. The wrapper does not install, log in to, or configure OpenCode.

Keep the consultation explicit, bounded, and brief. Do not invoke it implicitly for routine work.
