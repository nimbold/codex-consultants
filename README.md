# Codex Consultants

A Codex plugin for bounded, read-only Agy, Hermes, and OpenCode code-review second opinions. It uses a shared control plane inspired by the job, state, process, and review-target architecture of OpenAI's [Codex plugin for Claude Code](https://github.com/openai/codex-plugin-cc), while remaining a native Codex plugin.

Codex remains the primary investigator, implementer, tester, and decision-maker. Consultants never edit, commit, push, or become the sole source of a finding.

## Providers

- Agy — Antigravity, default `Gemini 3.6 Flash (High)`.
- Hermes — NVIDIA NIM, default `thinkingmachines/inkling` with `max` reasoning; MiniMax M3 remains available explicitly.
- OpenCode — OpenCode Zen, default `opencode/laguna-s-2.1-free` with the `high` reasoning variant.

Install and authenticate each client separately; the plugin does not bundle or configure them.

## Codex plugin installation

From a checkout:

```sh
codex plugin marketplace add /path/to/codex-consultants
codex plugin add codex-consultants@codex-consultants
```

After publishing, a GitHub checkout can use:

```sh
codex plugin marketplace add nimbold/codex-consultants --ref main
codex plugin add codex-consultants@codex-consultants
```

Start a new Codex thread after installation so skills and plugin metadata are rediscovered.

Codex Desktop currently exposes plugin skills in the composer picker. Type `/cons` and select `Codex Consultants: Consult` (the native `/consult` entry point), or select `Codex Consultants: Codex Consult` for the full control-plane skill. The accompanying request becomes the bounded consultation prompt. The plugin also retains the `commands/` templates for command-capable runtimes, but those files are not guaranteed to appear as direct Desktop slash commands. No global `codex-consult` executable is required for the plugin; the optional manual installer below adds that executable for terminal use.

## Control-plane commands

Use the shared runtime for one consultant or a provider panel:

```sh
codex-consult setup
codex-consult consult --provider agy "review the retry boundary"
codex-consult consult --provider hermes "look for lifecycle races"
codex-consult consult --provider opencode "challenge this design"
codex-consult review --provider all --background
codex-consult adversarial-review --provider all --background "look for stale state, cancellation, and security gaps"
codex-consult status
codex-consult result
codex-consult cancel <job-id>
```

The control-plane commands mirror these entrypoints as `/consult`, `/consult-review`, `/consult-adversarial-review`, `/consult-status`, `/consult-result`, `/consult-cancel`, and `/consult-setup` where the host supports plugin commands. In Codex Desktop, use the native `/consult` skill or `$codex-consult` and have Codex run the corresponding runtime operation. Use `--wait` with `--background` to retain durable job state while waiting for completion. Use `--json` for automation.

The existing `$agy-consult`, `$hermes-consult`, and `$opencode-consult` skills remain available for direct provider-specific consultations. `/opencode` remains as a compatibility command.

## Prompts and use cases

The reusable review prompts live in `plugins/codex-consultants/skills/codex-consult/prompts/` and are loaded by the runtime. They require evidence-backed findings, explicit uncertainty, and a read-only consultation boundary.

Normal review before shipping:

```sh
codex-consult review --provider all --scope working-tree --background
codex-consult status
codex-consult result
```

Review a clean feature branch against `main`:

```sh
codex-consult review --provider opencode --scope branch --base main --wait
```

Pressure-test a risky change from several angles:

```sh
codex-consult adversarial-review --provider all --background \
  "look for cancellation races, stale state, malformed input, recovery gaps, and security boundary bypasses"
```

Ask one provider a focused question without starting a panel:

```sh
codex-consult consult --provider hermes --path src/queue.rs \
  "trace admission and cancellation ordering; identify only issues supported by the supplied context"
```

If a long-running job must stop, use the exact id shown by `status`; cancellation records the request first and cleans up the worker and provider process trees. A cancellation or unavailable provider is not a code-quality verdict.

## Runtime design

The shared control plane owns the lifecycle that the provider adapters should not duplicate:

- repository-scoped, mode-600 job records under the Codex state directory;
- atomic state writes, bounded logs, session-aware status filtering, and stale-worker reconciliation;
- foreground or detached background jobs with provider fan-out and partial-success reporting;
- process-group cancellation across the worker and provider subprocesses;
- durable `status`, `result`, `cancel`, and `setup` operations.

Each adapter still builds a bounded task bundle, omits sensitive paths and full lockfiles, materializes only selected context into a temporary workspace, and uses provider-specific read-only configuration. The real repository path is not supplied as consultant context.

Empty output, timeouts, non-zero exits, missing clients, and partial provider availability are inconclusive. Codex must independently verify every actionable claim against live source, tests, logs, and repository state.

## Manual installation

The optional installer adds the control-plane skill, provider skills, launchers, and global guidance:

```sh
git clone https://github.com/nimbold/codex-consultants.git
cd codex-consultants
./scripts/install.sh --install-guidance
```

The main launcher is `codex-consult`. Compatibility launchers remain available as `codex-agy-consult`, `codex-hermes-consult`, `codex-opencode`, and `codex-opencode-consult`.

Use either plugin installation or the manual installer for a given `CODEX_HOME`, not both. Add `--force` only when replacing an existing installation; the installer creates timestamped backups.

## Development

Run the local checks from the repository root:

```sh
python3 -m py_compile \
  plugins/codex-consultants/skills/codex-consult/scripts/consultant_runtime.py \
  plugins/codex-consultants/skills/agy-consult/scripts/agy_consult.py \
  plugins/codex-consultants/skills/hermes-consult/scripts/hermes_consult.py \
  plugins/codex-consultants/skills/opencode-consult/scripts/opencode_consult.py \
  scripts/install.py
python3 scripts/test_consult.py
python3 scripts/test_hermes_consult.py
python3 scripts/test_opencode_consult.py
python3 scripts/test_runtime.py
python3 scripts/test_install.py
```

Live provider smoke tests remain opt-in because they require authenticated local sessions and may consume quota.

## Privacy

Review bundles may contain source code and are sent to the configured provider. Never include credentials, cookies, tokens, private keys, databases, or unrelated private data. Review each provider's current terms and data controls before using this with sensitive repositories.

## Credits

The shared lifecycle and review-target design was informed by OpenAI's [codex-plugin-cc](https://github.com/openai/codex-plugin-cc), especially its durable jobs, background execution, status/result/cancel flow, and normal/adversarial review split. This repository is an independent Codex plugin that retains its Agy, Hermes, and OpenCode adapters and their provider-specific safety boundaries.
