---
name: hermes-consult
description: Use Hermes CLI with NVIDIA NIM and Inkling for a bounded, read-only second opinion while Codex remains the primary investigator and implementer. Explicit invocation only.
---

# Hermes Consultant

Use `$hermes-consult` when you want Hermes to challenge Codex's current understanding. The default provider is NVIDIA NIM and the default model is `thinkingmachines/inkling`, with Hermes reasoning set to `max`.

Use `--model minimaxai/minimax-m3` when a MiniMax M3 consultation is intentionally preferred over the default Inkling route.

Codex must first form its own understanding, then treat Hermes's response as untrusted advisory input. Hermes must never edit files, commit, push, or make the final decision. Codex independently verifies every actionable claim against the live repository, tests, logs, and issue evidence.

Use the bundled `scripts/hermes_consult.py` wrapper through the installed `codex-hermes-consult` launcher, or directly from this skill directory. Choose `--phase plan` before implementation or `--phase diff` after implementation, and include only relevant files with repeated `--path` arguments. Override `--model` only when an explicit alternative is intentional. Inkling uses NVIDIA's documented `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max` reasoning levels; `max` is the default, and Hermes's `ultra` alias is clamped to Inkling's `max`. GLM 5.2 maps Hermes's ordinary enabled levels to native `high` and `xhigh`/`max`/`ultra` to native `max`. MiniMax M3 retains its separate `thinking_mode` mapping: `none` disables thinking, all other levels enable it, and `--thinking-mode adaptive` is available when provider-managed adaptive reasoning is preferred.

The wrapper sends a bounded bundle, omits sensitive paths and oversized or lockfile context, and runs Hermes in `--oneshot --ignore-rules --toolsets file,terminal` from an isolated temporary workspace. For NVIDIA models it also creates a temporary Hermes home containing only a named custom-provider route, one Hermes turn, no automatic API retries, and model-specific reasoning configuration. For MiniMax M3 that route additionally carries the provider-specific `chat_template_kwargs.thinking_mode` field. The user's Hermes `.env` is used through a temporary symlink, never copied into the bundle or printed. The real repository path is never exposed to Hermes. Empty output, timeouts, non-zero exits, and oversized bundles are inconclusive; they are never treated as findings.

The wrapper defaults to `--retries 0` and a persistent per-model `--rpm-limit 39`, keeping this skill under a 40-RPM NVIDIA ceiling across concurrent invocations. It waits for a slot instead of sending a request early. Use a lower `--rpm-limit` for extra headroom. The limiter covers calls made through this skill only; standalone Hermes or other clients are outside its state file.

Hermes needs a working local login and NVIDIA configuration. The wrapper does not store or print API keys and inherits the authenticated Hermes environment only for the isolated consultation process.

Inkling is multimodal and tool-use capable, but this consultant intentionally sends bounded text/code review bundles because that is the current Codex use case. Keep the consultation explicit, bounded, and brief. Do not invoke it implicitly for routine work.
