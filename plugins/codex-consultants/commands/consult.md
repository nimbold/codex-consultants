---
description: Run a bounded Agy, Hermes, OpenCode, or multi-provider consultation with durable job controls.
---

# Consultant Control Plane

Use the shared runtime so provider calls can be run in parallel and followed through `status`, `result`, and `cancel`.

```sh
codex-consult consult --provider all $ARGUMENTS
```

Choose a provider explicitly when a panel is not needed:

```sh
codex-consult consult --provider agy $ARGUMENTS
codex-consult consult --provider hermes $ARGUMENTS
codex-consult consult --provider opencode $ARGUMENTS
```

Keep the consultation read-only, bounded to relevant paths, and advisory. Codex independently verifies every actionable claim.
