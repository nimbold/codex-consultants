---
name: agy-consultant
description: Use the installed Antigravity CLI (agy) as a read-only second-opinion consultant during non-trivial coding, debugging, architecture, release, security, or code-review work. Codex must independently inspect, validate, decide, edit, and test; use this when broader-scope adversarial review would help or when the user explicitly asks for an agy consultation.
---

# Agy Code Consultant

Use agy to challenge Codex's current understanding, not to replace Codex's reasoning or implementation work.

This is an explicit opt-in skill. Do not invoke it implicitly; use it only when the user explicitly requests an agy consultation, such as with `$agy-consultant` or "consult agy".

## Operating contract

- Codex remains the primary investigator, implementer, tester, and decision-maker.
- Form an initial Codex hypothesis or plan before consulting agy when the task is non-trivial.
- Treat every agy statement as an untrusted advisory claim. Verify it against live code, issue evidence, logs, and tests before acting.
- Never ask agy to edit the working tree, commit, push, or make the final decision.
- Never copy an agy patch blindly. Translate accepted advice into a Codex-owned change.
- Keep the consultation bounded to the task, relevant files, and diff. Do not send secrets, cookies, tokens, `.env` files, private keys, databases, or unrelated personal data.
- The wrapper defaults to one pass with `Gemini 3.5 Flash (High)`. It uses a 120-second agy print deadline, an 80,000-byte bundle limit, and one transient retry. Repeat `--model` for one explicit second opinion, or use `--print-timeout`, `--max-bytes`, or `--retries` when a different tradeoff is intentional.
- Each pass must return a compact report with at most four findings. The wrapper keeps only the report, finding, uncertainty, and no-finding lines and caps the result before it reaches Codex, so independent review does not expand Codex's context unnecessarily.

## When to consult

- Consult before implementation when the task spans multiple files, crosses frontend/backend or repository boundaries, changes a public contract, or has meaningful race, security, release, or compatibility risk.
- Consult after implementation when there is a meaningful diff and an adversarial pass could find missed consumers, regressions, edge cases, or worst-case behavior.
- Skip trivial wording, formatting, or one-line changes unless the user explicitly requests consultation.

## Workflow

1. Inspect the live repository and establish Codex's own initial understanding.
2. Choose `plan` for scope/architecture consultation or `diff` for review of an implemented change.
3. Select only relevant context files with repeated `--path` arguments. The wrapper preflights both phases: full lockfiles are omitted, oversized full files are left out with an explicit context note, and diff mode keeps only bounded relevant hunks instead of bundling every lockfile and monolithic file.
4. Invoke `codex-agy-consult` when the manual installer has installed it. When using the automatic plugin path without the launcher, invoke the bundled `scripts/agy_consult.py` from this skill directory directly. For example:

   ```sh
   codex-agy-consult --phase plan --path src/relevant/module.ts <<'EOF'
   Challenge Codex's current plan for this task. Look for missing consumers, hidden coupling, races, security boundaries, compatibility breaks, and normal/worst-case behavior.
   Return a compact report with at most four evidence-based findings. If the supplied context is insufficient, say INSUFFICIENT_CONTEXT instead of guessing.
   EOF
   ```

5. Read the consultation output completely. Separate concrete findings from hypotheses and note any claimed access that was not present in the supplied bundle.
6. Re-open the relevant live code and independently validate each actionable finding. Use tests, logs, and runtime behavior where appropriate.
7. Decide what to ignore, investigate, or implement. Make edits and run verification as Codex work.
8. Report that agy was consulted, summarize accepted/rejected findings briefly, and state the remaining uncertainty.

## User-visible progress

- Keep progress concise: one short update before the consultation and one short result after it completes.
- Do not narrate bundle construction, token or safety-limit details, retry reasoning, prompt contents, or private chain-of-thought in visible updates. Summarize only the observable outcome and the decision.
- The wrapper preflights and narrows oversized context before invoking agy. If the remaining bundle still cannot fit, report the omitted paths and continue ordinary Codex work; do not add another model unless the user explicitly asks.
- Do not paste the full consultation prompt or command output unless the user asks for it.

## Consultation prompt requirements

- Tell agy it is read-only and must review only the supplied task, status, files, and diff.
- Require an explicit `INSUFFICIENT_CONTEXT` result when the bundle cannot support a claim.
- Require the compact `FINDING:` format with severity, FACT/HYPOTHESIS, file/line or symbol, concrete evidence, impact, normal/worst-case scenario, confidence, and the next verification step.
- Require agy to distinguish observed facts from hypotheses and never claim to have read files, commits, logs, or tools that were not supplied.
- Prefer a different model family from the active Codex model when the user wants maximum opinion diversity, but keep the default agy account/model unless a choice is requested.

## Safety and failure handling

- The wrapper invokes `agy` in `plan` mode with `--sandbox` from a temporary workspace containing only the supplied context files; the isolated workspace keeps tool activity away from the real worktree while preventing missing-file loops for supplied context.
- Do not add `--dangerously-skip-permissions` or `--add-dir` to the consultation command.
- An empty response, non-zero exit, timeout, or oversized bundle is an unavailable/inconclusive consultation, not evidence.
- Do not silently truncate prompts or diffs. Preflight omissions must be listed in the supplied context notes, and Codex must validate them against the live repository.
- Do not let a consultation failure block ordinary Codex work unless the user explicitly made consultation a required gate.

The deterministic bundle-and-run implementation is in [scripts/agy_consult.py](scripts/agy_consult.py). The repository installer creates the optional global `codex-agy-consult` launcher; the plugin itself does not assume a fixed install path.
