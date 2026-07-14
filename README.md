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

Choose one installation path per Codex home. The manual global skill and the marketplace plugin expose the same consultant; installing both can create duplicate autocomplete entries.

## How it works

Codex first forms its own understanding. The consultant then receives a bounded task bundle containing selected files, safe repository status, and, for diff consultations, the tracked diff. The wrapper invokes `agy` in an isolated plan/sandbox session with only the selected files materialized in a temporary workspace; it never gives agy the repository path. Codex validates each advisory finding against the live repository before changing anything.

The wrapper fails closed on oversized bundles, sensitive paths, out-of-repository paths, timeouts, empty output, and non-zero `agy` exits. Plan consultations omit the tracked diff and run agy in read-only plan mode. Agy receives only the supplied context files in its isolated temporary workspace, and one transient failure retry is performed within the overall timeout. It never silently truncates context and never edits, commits, or pushes.

The wrapper uses `Gemini 3.5 Flash (High)`, an `80,000`-byte bundle limit, one retry, and a `120s` agy print-mode deadline by default, independent of the interactive model selected in the local agy settings. Repeat `--model` to request up to three independent, sequential opinions; for example:

```sh
codex-agy-consult \
  --model "Gemini 3.5 Flash (High)" \
  --model "Gemini 3.1 Pro (High)" \
  --phase diff \
  "Review the current diff and report independent material risks."
```

Each model receives the same bounded bundle and its output is labeled separately. Codex compares and validates the opinions; the wrapper does not ask agy to synthesize a final decision. Override `--print-timeout` when a different latency tradeoff is intentional.

Models run sequentially to avoid a burst of simultaneous requests. If one model times out or fails, successful responses are still returned and the unavailable model is reported; if all requested models fail, the consultation is inconclusive.

For a concise Codex interaction, progress should be limited to a short pre-consultation update and a short result. Bundle construction, safety-limit handling, retry reasoning, prompt contents, and private chain-of-thought should not be narrated. An oversized bundle may be narrowed and retried once; an empty, timed-out, or failed retry ends the consultation unless the user explicitly requests more attempts.

## Invocation policy

The consultant is explicit-only by default. Installing or enabling the plugin makes the skill available, but does not run `agy` automatically. Ask Codex to use `$agy-consultant` or say "consult agy" when you want a second opinion. Codex still performs the complete investigation, implementation, review, and testing, and decides which advice to accept.

## Development

Run the local checks from the repository root:

```sh
python3 -m py_compile plugins/codex-agy-consultant/skills/agy-consultant/scripts/agy_consult.py
python3 scripts/test_install.py
```

The live `agy` smoke test is intentionally opt-in because it requires an authenticated local Antigravity session.

## Privacy

Review bundles may contain source code and are sent to the configured Antigravity service. Do not include credentials, cookies, tokens, private keys, databases, or unrelated private data. Review the current Antigravity terms and data controls before using this with sensitive repositories.
