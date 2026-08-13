---
name: verify-against-manifest-numbers
description: When a frozen contract exists, check implementation constants and touched-file set against the manifest itself, not against the tests
metadata:
  type: feedback
---

When this repo has a frozen contract (`execution-manifest.yaml` +
a matching plan markdown), review the implementation against the manifest's
literal numbers and file sets — not against the test suite.

**Why:** tests are edited in the same change as the implementation. A run of this
review found `_EXCHANGE_CACHE_SECONDS` moved 15 -> 45 and
`_EXCHANGE_TRUTH_TIMEOUT_SECONDS` 8 -> 20 against
`machine_contract.runtime_api.exchange_probe`, with a *new* test asserting the new
value (`cache window > 30s console poll`). The suite was fully green and the drift
was invisible from test output alone. The same review found ~10 modified paths
outside the cumulative `must_change`/`may_change`/`planned_new_test_files`
whitelist, which E-009 defines as a red-test condition (`whitelist_escape`).

**How to apply:** Build the cumulative whitelist by unioning every task's
`must_change` + `may_change` + `planned_new_test_files`, diff it against
`git status --porcelain --untracked-files=all`, and grep the implementation for each
frozen numeric in `machine_contract`. Also check `commit_allowed` / `status` against
actual `git log` — a manifest still saying `commit_allowed: false` while
implementation commits exist means the manifest is no longer an accurate record.
