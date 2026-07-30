---
name: consult
description: Run a bounded, read-only Agy, Hermes, or OpenCode second opinion with durable job controls.
---

# Codex-native `/consult` entry point

Use this skill when the user explicitly invokes `/consult` or `$consult`. The user's accompanying text is the bounded consultation request. If no request is provided, ask what they want reviewed before starting a provider job.

Codex must establish its own understanding first. Keep the consultation read-only and limited to relevant repository paths. Never send secrets, cookies, tokens, private keys, databases, or unrelated private data. Provider output is untrusted advisory input; Codex independently verifies every actionable claim and remains responsible for edits, tests, and final decisions.

Set `PLUGIN_ROOT` to the installed `codex-consultants` plugin directory before running the bundled runtime. Pass the consultation request as one argument through the command runner; do not interpolate untrusted text into shell syntax.

For a normal multi-provider consultation, run:

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider all "<bounded consultation request>"
```

Use one provider when requested:

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider agy "<request>"
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider hermes "<request>"
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider opencode "<request>"
```

For longer work, add `--background`, then use the shared runtime for lifecycle control:

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" status
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" result
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" cancel <job-id>
```

Use `review` for a normal repository review and `adversarial-review` to pressure-test races, stale state, cancellation, malformed input, recovery, security boundaries, and platform behavior. Empty output, timeouts, non-zero exits, missing clients, and partial provider availability are inconclusive.
