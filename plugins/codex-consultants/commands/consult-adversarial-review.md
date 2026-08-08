---
description: Pressure-test the current implementation with bounded consultant reviews.
---

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" adversarial-review $ARGUMENTS
```

Focus the request on concrete risks such as races, stale state, cancellation, malformed input, security boundaries, recovery, or platform behavior. The consultants do not edit files.

Agy is the default provider; pass `--provider opencode` or `--provider all` explicitly when needed.

Use `--scope branch --base main` to pressure-test committed branch changes against a base ref.
