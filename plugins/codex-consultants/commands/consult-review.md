---
description: Run a normal bounded review through the selected consultant providers.
---

```sh
python3 "$PLUGIN_ROOT/skills/codex-consult/scripts/consultant_runtime.py" review $ARGUMENTS
```

Add `--background` for long reviews, then use `/consult-status` and `/consult-result`. Agy is the default; use `--provider opencode` for OpenCode or `--provider all` for a full panel.

For a clean branch, pass `--scope branch --base main`; the default `auto` scope reviews the working tree when it is dirty.
