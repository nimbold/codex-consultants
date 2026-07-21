# Codex Consultants

A Codex plugin for bounded, read-only Agy, Hermes, and OpenCode code-review second opinions, with NVIDIA NIM, Thinking Machines Inkling, MiniMax M3, and OpenCode Zen free-model support.

Codex remains the primary investigator, implementer, tester, and decision-maker.

The plugin does not bundle any client. Install and authenticate the client you want separately, then keep it on `PATH`. Hermes is configured for NVIDIA NIM with Thinking Machines Inkling and `max` reasoning by default; MiniMax M3 remains available as an explicit alternative. OpenCode defaults to the currently free OpenCode Zen `opencode/laguna-s-2.1-free` with its `high` reasoning variant, with other free Zen models available explicitly.

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

Start a new Codex thread after installation so the skills are rediscovered. The plugin does not install, log in to, or configure Agy, Hermes, or NVIDIA. Hermes defaults to NVIDIA's `thinkingmachines/inkling` with `max` reasoning.

For OpenCode, install the CLI separately, authenticate OpenCode Zen with `opencode auth login`, and confirm the catalog with `opencode models opencode`. The free Zen choices are provider-managed and may change; the wrapper currently defaults to `opencode/laguna-s-2.1-free` with `high` reasoning and also accepts `opencode/deepseek-v4-flash-free`, `opencode/big-pickle`, `opencode/mimo-v2.5-free`, `opencode/north-mini-code-free`, and `opencode/nemotron-3-ultra-free`.

Use either the plugin installation or the manual installer for a given `CODEX_HOME`, not both. If upgrading from the old repository, remove its stale registration once with `codex plugin remove codex-agy-consultant@codex-agy` before installing this plugin.

## Skill commands

Use `/skills` to browse the installed skills, or mention these concise skill names directly:

- `$agy-consult` — bounded Agy second opinion using `Gemini 3.6 Flash (High)` by default.
- `$hermes-consult` — bounded Hermes second opinion using NVIDIA NIM and `thinkingmachines/inkling` with `max` reasoning.
- `$opencode-consult` — bounded OpenCode CLI second opinion using OpenCode Zen free models, mainly Laguna S 2.1 Free with `high` reasoning.

The plugin also provides the short `/opencode` command for the default OpenCode review. Use `$opencode-consult` when you need its advanced options such as `--phase`, `--path`, `--model`, or `--variant`.

All skills are explicit-only. They do not run automatically, and Codex must independently verify every actionable suggestion.

## Manual installation

The optional installer adds the concise Agy, Hermes, and OpenCode skills and command-line launchers:

```sh
git clone https://github.com/nimbold/codex-consultants.git
cd codex-consultants
./scripts/install.sh --install-guidance
```

Use `python3 scripts/install.py` when `install.sh` is unavailable. Add `--force` only when replacing an existing installation; the installer creates timestamped backups. `--install-guidance` is optional and appends a marked, idempotent policy block to `CODEX_HOME/AGENTS.md`.

The launchers are:

```sh
codex-agy-consult
codex-hermes-consult
codex-opencode
codex-opencode-consult
```

## How it works

Codex first forms its own understanding. The selected consultant then receives a bounded task bundle containing the task, safe repository status, selected files, and, for diff consultations, relevant tracked hunks. The shared preflight omits sensitive paths, full lockfiles, oversized files, and low-priority diff paths with explicit context notes.

Each wrapper invokes its client from an isolated temporary workspace containing only the selected context files. Agy uses plan/sandbox mode. Hermes uses NVIDIA NIM, Inkling with `max` reasoning by default, `--oneshot`, `--ignore-rules`, and an isolated custom-provider route. MiniMax M3 remains available with its provider-specific thinking mode. OpenCode uses `opencode run` with a temporary read-only config, the Laguna S 2.1 Free `high` variant by default, and external plugins disabled. None of these wrappers gives the client the real repository path or asks it to edit, commit, push, or make the final decision.

Empty output, timeouts, non-zero exits, and oversized bundles are inconclusive. Reports are compacted to a bounded line-based format with at most four findings. Codex validates the result against the live repository before accepting or rejecting any advice.

## Development

Run the local checks from the repository root:

```sh
python3 -m py_compile \
  plugins/codex-consultants/skills/agy-consult/scripts/agy_consult.py \
  plugins/codex-consultants/skills/hermes-consult/scripts/hermes_consult.py \
  plugins/codex-consultants/skills/opencode-consult/scripts/opencode_consult.py \
  scripts/install.py
python3 scripts/test_consult.py
python3 scripts/test_hermes_consult.py
python3 scripts/test_opencode_consult.py
python3 scripts/test_install.py
```

Live client smoke tests are intentionally opt-in because they require authenticated local Agy, Hermes, or OpenCode sessions and may consume provider quota.

## Privacy

Review bundles may contain source code and are sent to the configured provider. Do not include credentials, cookies, tokens, private keys, databases, or unrelated private data. Review the current Agy, Hermes, NVIDIA, and OpenCode Zen terms and data controls before using this with sensitive repositories.
