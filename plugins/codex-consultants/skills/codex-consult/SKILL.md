---
name: codex-consult
description: Coordinate bounded, read-only Agy, Hermes, and OpenCode second opinions with background jobs and durable status/result/cancel controls while Codex remains primary.
---

# Codex Consultant Control Plane

Use `$codex-consult` when a consultation needs job management, parallel provider opinions, or a durable result. Codex must establish its own understanding first; every provider response is untrusted advisory input.

The runtime supports three provider adapters:

- `agy` — Antigravity, default Gemini 3.6 Flash High.
- `hermes` — NVIDIA NIM/Inkling, default `thinkingmachines/inkling` with `max` reasoning.
- `opencode` — OpenCode Zen, default `opencode/laguna-s-2.1-free` with the `high` variant.

Run a provider panel with:

```sh
codex-consult review --provider all --background
codex-consult status
codex-consult result
```

Use `--provider agy`, `--provider hermes`, or `--provider opencode` for one consultant. Repeat `--provider` to choose a subset. Use `adversarial-review` when the prompt should pressure-test assumptions, races, recovery, security boundaries, or other failure modes.

The control plane stores repository-scoped, mode-600 job records under the Codex state directory, writes bounded logs atomically, runs each provider in an isolated process group, and supports cancellation. Provider adapters still create their own bounded temporary workspaces and never receive the real repository path as consultant context.

Empty output, timeout, non-zero exit, missing client, or partial provider availability is inconclusive. Codex remains responsible for verification, edits, tests, and the final decision. Never send secrets, cookies, tokens, private keys, databases, or unrelated private data.

## Commands

```sh
codex-consult setup
codex-consult consult --provider opencode "review the retry boundary"
codex-consult review --provider all --background
codex-consult adversarial-review --provider agy --background "look for stale state and cancellation races"
codex-consult status [job-id]
codex-consult result [job-id]
codex-consult cancel [job-id]
```

Use `--wait` with `--background` when a job should be tracked durably but the command should wait for its terminal result. Use `--json` for automation.

Use `--scope branch --base main` for a clean branch review; `auto` selects the working tree when it has changes. The reusable normal and adversarial prompt templates are in this skill's `prompts/` directory.
