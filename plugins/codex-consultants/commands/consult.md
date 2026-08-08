---
description: Run a bounded Agy consultation by default, or explicitly choose OpenCode or a multi-provider panel.
---

# Consultant Control Plane

Use the shared runtime so provider calls can be run in parallel and followed through `status`, `result`, and `cancel`.

Set `PLUGIN_ROOT` to the installed plugin directory before running the bundled runtime.

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult $ARGUMENTS
```

Choose a provider explicitly when a panel is not needed:

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider agy $ARGUMENTS
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider opencode $ARGUMENTS

Use `--provider all` explicitly when a multi-provider panel is wanted.
```

Keep the consultation read-only, bounded to relevant paths, and advisory. Codex independently verifies every actionable claim.
