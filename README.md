# Codex Agy Consultant

Use Google Antigravity CLI (`agy`) as a bounded, read-only second opinion while Codex remains the primary investigator, implementer, tester, and decision-maker.

`agy` is not bundled. Install it separately, sign in, and keep it on `PATH`.

## Manual installation

Clone this repository, then install the global skill and launcher:

```sh
git clone https://github.com/OWNER/codex-agy-consultant.git
cd codex-agy-consultant
./scripts/install.sh --install-guidance
```

Use `python3 scripts/install.py` when `install.sh` is unavailable. Add `--force` only when replacing an existing installation; the installer creates timestamped backups. `--install-guidance` is optional and appends a marked, idempotent policy block to `CODEX_HOME/AGENTS.md`.

## Automatic Codex plugin installation

The repository includes a repo-local marketplace entry. From a checkout:

```sh
codex plugin marketplace add /path/to/codex-agy-consultant
codex plugin add codex-agy-consultant@codex-agy
```

After publishing, the same path can use a GitHub source:

```sh
codex plugin marketplace add OWNER/codex-agy-consultant --ref main
codex plugin add codex-agy-consultant@codex-agy
```

Start a new Codex thread after installation so the skill is rediscovered. Automatic plugin installation does not install `agy` or authenticate a Google account.

## How it works

Codex first forms its own understanding. The consultant then receives a bounded task bundle containing selected files, safe repository status, and tracked diff. The wrapper invokes `agy` in plan/sandbox mode from an empty temporary directory. Codex validates each advisory finding against the live repository before changing anything.

The wrapper fails closed on oversized bundles, sensitive paths, out-of-repository paths, timeouts, empty output, and non-zero `agy` exits. It never silently truncates context and never edits, commits, or pushes.

## Development

Run the local checks from the repository root:

```sh
python3 -m py_compile plugins/codex-agy-consultant/skills/agy-consultant/scripts/agy_consult.py
python3 scripts/test_install.py
```

The live `agy` smoke test is intentionally opt-in because it requires an authenticated local Antigravity session.

## Privacy

Review bundles may contain source code and are sent to the configured Antigravity service. Do not include credentials, cookies, tokens, private keys, databases, or unrelated private data. Review the current Antigravity terms and data controls before using this with sensitive repositories.
