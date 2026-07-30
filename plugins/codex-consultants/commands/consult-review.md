---
description: Run a normal bounded review through the selected consultant providers.
---

```sh
codex-consult review --provider all $ARGUMENTS
```

Add `--background` for long reviews, then use `/consult-status` and `/consult-result`. Use `--provider agy`, `--provider hermes`, or `--provider opencode` to avoid a full panel.

For a clean branch, pass `--scope branch --base main`; the default `auto` scope reviews the working tree when it is dirty.
