---
description: Run a bounded, read-only OpenCode CLI second opinion using the default Laguna S 2.1 Free model.
---

# OpenCode Consultation

## Preflight

1. Form Codex's own understanding of the task before consulting OpenCode.
2. Identify only the relevant repository paths; never include credentials, cookies, tokens, private keys, databases, or unrelated private data.
3. Confirm that OpenCode CLI is installed and authenticated for OpenCode Zen.
4. Confirm the task is asking for a second opinion, not for OpenCode to edit the repository.

## Plan

- Run the bounded consultant wrapper in `diff` mode by default, or `plan` mode when the implementation has not started.
- Use OpenCode Zen's `opencode/laguna-s-2.1-free` with its `high` reasoning variant by default.
- Keep the consultation read-only and independently verify every actionable claim against the live repository.

## Commands

Use `codex-opencode` when the short launcher is installed:

```bash
codex-opencode "$ARGUMENTS"
```

If the short launcher is unavailable, use the compatibility launcher:

```bash
codex-opencode-consult "$ARGUMENTS"
```

Pass relevant files explicitly when the task needs source context:

```bash
codex-opencode --path path/to/relevant-file --path path/to/another-file "$ARGUMENTS"
```

Use `--phase plan` before implementation, `--phase diff` after implementation, and repeat `--model` only when independent free-model opinions are intentionally requested.

## Verification

1. Treat empty output, timeouts, non-zero exits, and unavailable models as inconclusive.
2. Check that the response uses the bounded `REPORT`, `FINDING`, and optional `UNCERTAINTY` contract.
3. Verify each finding against the live source, tests, logs, and repository state.
4. Confirm that the consultation did not modify the real checkout.

## Summary

Report the selected model, consultation phase, whether the wrapper completed, and which findings Codex independently confirmed or rejected.

## Next Steps

Continue the repository investigation and implementation using Codex's verified conclusions. Do not treat OpenCode's response as the final decision.
