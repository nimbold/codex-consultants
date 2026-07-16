# Codex Consultants

A Codex plugin for bounded, read-only Agy and Hermes code-review second opinions, with NVIDIA NIM and MiniMax M3 support.

Codex remains the primary investigator, implementer, tester, and decision-maker.

The plugin does not bundle either client. Install and authenticate the client you want separately, then keep it on `PATH`. Hermes is configured for NVIDIA NIM with MiniMax M3 by default.

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

Start a new Codex thread after installation so the skills are rediscovered. The plugin does not install, log in to, or configure Agy, Hermes, or NVIDIA.

## Skill commands

Use `/skills` to browse the installed skills, or mention these concise skill names directly:

- `$agy-consult` — bounded Agy second opinion using the existing Gemini configuration.
- `$hermes-consult` — bounded Hermes second opinion using NVIDIA NIM and `minimaxai/minimax-m3`.

`$agy-consultant` remains available as a compatibility name for existing users. Codex's portable plugin interface uses skills (`$name`); arbitrary `/agy-...` and `/hermes-...` slash commands are not currently a distributable plugin surface. The old local `/prompts:name` mechanism is local-only and deprecated, so it is not used here.

Both skills are explicit-only. They do not run automatically, and Codex must independently verify every actionable suggestion.

## Manual installation

The optional installer adds the concise skills, the legacy Agy skill, and command-line launchers:

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
```

## How it works

Codex first forms its own understanding. The selected consultant then receives a bounded task bundle containing the task, safe repository status, selected files, and, for diff consultations, relevant tracked hunks. The shared preflight omits sensitive paths, full lockfiles, oversized files, and low-priority diff paths with explicit context notes.

Each wrapper invokes its client from an isolated temporary workspace containing only the selected context files. Agy uses plan/sandbox mode. Hermes uses NVIDIA NIM, MiniMax M3 by default, `--oneshot`, `--safe-mode`, `--ignore-rules`, and `--ignore-user-config`. Neither wrapper gives the client the real repository path or asks it to edit, commit, push, or make the final decision.

Empty output, timeouts, non-zero exits, and oversized bundles are inconclusive. Reports are compacted to a bounded line-based format with at most four findings. Codex validates the result against the live repository before accepting or rejecting any advice.

## Development

Run the local checks from the repository root:

```sh
python3 -m py_compile \
  plugins/codex-consultants/skills/agy-consultant/scripts/agy_consult.py \
  plugins/codex-consultants/skills/hermes-consult/scripts/hermes_consult.py \
  scripts/install.py
python3 scripts/test_consult.py
python3 scripts/test_hermes_consult.py
python3 scripts/test_install.py
```

Live client smoke tests are intentionally opt-in because they require authenticated local Agy or Hermes sessions and may consume provider quota.

## Privacy

Review bundles may contain source code and are sent to the configured provider. Do not include credentials, cookies, tokens, private keys, databases, or unrelated private data. Review the current Agy, Hermes, and NVIDIA terms and data controls before using this with sensitive repositories.
