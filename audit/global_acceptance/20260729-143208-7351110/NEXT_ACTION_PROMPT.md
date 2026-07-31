# Next Action Prompt

> Do not execute this prompt until the operator confirms
> `GLOBAL_ACCEPTANCE_REPORT.md`.

You are continuing an independent, read-only global acceptance audit. The prior
run `20260729-143208-7351110` ended with `REJECTED_INTEGRATION` at Phase 0 because
the worktree was already dirty:

```text
?? docs/audit/AI-global-project-acceptance-audit-master-prompt.md
```

Your only next action is to rerun the complete audit from an operator-approved
clean checkout.

Rules:

1. Do not delete, move, commit, stash, reset, or overwrite user work without
   explicit operator instruction.
2. The operator must provide a clean checkout, or explicitly choose how the
   untracked master prompt is preserved while making the checkout clean.
3. Before creating a new evidence directory, run `git status --short`,
   `git rev-parse HEAD`, and lock the new HEAD.
4. If `git status --short` is non-empty, stop again with
   `REJECTED_INTEGRATION`; do not distinguish "harmless" local files.
5. If clean, create a new
   `audit/global_acceptance/<YYYYMMDD-HHMMSS-short-sha>/` and execute every
   eligible Phase 0–13 step in
   `docs/audit/AI-global-project-acceptance-audit-master-prompt.md`.
6. Start from one-click startup, formal Scheduler/production Task registration,
   and engine activation. Do not trust prior Gate summaries, test counts, audit
   evidence, or commit messages.
7. Preserve proof-type isolation. Stateful Strict Fake cannot prove Testnet;
   Contract cannot prove Natural Scheduler E2E; skipped real-environment checks
   are `BLOCKED_REAL_ENV`, never pass.
8. Never enable Mainnet, alter trading/risk parameters, or repair discovered
   defects during the audit.
9. Persist every command with command, working directory, environment-variable
   names only, timestamps, exit code, stdout, and stderr. Never persist secret
   values.
10. Produce a fresh report, manifest, strategy-readiness verdict, and exactly one
    next action. Do not execute that next action without operator confirmation.

This prompt authorizes no code change and no strategy optimization.
