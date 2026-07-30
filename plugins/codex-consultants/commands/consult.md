---
description: Run a bounded Agy, Hermes, OpenCode, or multi-provider consultation with durable job controls.
---

# Consultant Control Plane

Use the shared runtime so provider calls can be run in parallel and followed through `status`, `result`, and `cancel`.

Set `PLUGIN_ROOT` to the installed plugin directory before running the bundled runtime.

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider all $ARGUMENTS
```

Choose a provider explicitly when a panel is not needed:

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider agy $ARGUMENTS
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider hermes $ARGUMENTS
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" consult --provider opencode $ARGUMENTS
```

Keep the consultation read-only, bounded to relevant paths, and advisory. Codex independently verifies every actionable claim.
