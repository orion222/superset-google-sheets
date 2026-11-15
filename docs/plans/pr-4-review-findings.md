# PR #4 Review Findings

## Outcome

Verify each PR #4 review finding against the code, implement the valid findings, run the relevant backend checks, commit and push the changes to PR #4's existing head branch, and reply to every inline review comment. Do not merge the PR.

## Acceptance checks

- SQL Lab and chart exports use one delivery flow, return `spreadsheet_url`, and share one API registration — unit: backend/tests/test_sqllab_api.py and backend/tests/test_chart_api.py `#shared-export-contract`
- Exception matching handles subclasses and restricts Marshmallow validation errors; query-time `ValueError` remains a server error — unit: backend/tests/test_sqllab_api.py and backend/tests/test_chart_api.py `#exception-classification`
- The chart endpoint validates its body before calling the domain function, which accepts a typed request — unit: backend/tests/test_chart_api.py `#typed-chart-request`
- Chart query row limits are bounded at `sync_max_rows() + 1` without mutating the request — unit: backend/tests/test_chart_api.py `#chart-row-cap`
- Capability reporting matches the export permission check — unit: backend/tests/test_chart_api.py `#capability-parity`
- Chart tabs use the real Superset name builder and remain unique after truncation — unit: backend/tests/test_chart_api.py `#chart-tab-deduplication`
- The complete backend suite and focused static checks pass — unit: backend/tests `#backend-verification`
- The pushed commit is PR #4's head and every inline finding has a reply — manual: GitHub PR #4 `#pushed-pr-responses`

## Approach

Use shared backend helpers for authorization, row-cap rejection, spreadsheet delivery, and safe endpoint errors. Parse chart HTTP input at the route boundary into `ChartExportRequest`; keep chart-specific tab labels with chart export code. Preserve the `spreadsheet_url` response contract for both export routes. Push only to the existing PR #4 head branch and reply in the existing inline threads; do not merge.

## Decisions

- Both export routes return `spreadsheet_url`, as the frontend contract expects.
- `errors.is_named_exception` stays public and generic so higher stacked PRs can reuse it; all changed exception-name checks route through it.
- Chart query execution errors other than recognized security or Marshmallow validation errors bubble to the common generic 500 response.
- Keep the typed request wrapper small (`payload`, `chart_name`) so PR #11 can add `chart_id: int | None` without changing `export_chart`'s argument type.
- Row-cap rejection uses one message, and chart queries are capped to one row over the configured maximum before execution.
- In the follow-up request, the user authorized committing and pushing to the existing PR #4 branch and replying to inline comments; no merge was authorized.

## Tasks

### Start
- [x] Fetch the PR review summary and all inline comments `#fetch-pr-review`
- [x] Verify each finding against the original code and the relevant call paths `#verify-findings`

### Plan
- [x] Record implementation decisions and acceptance checks `#review-plan`
- [x] Re-scope the delivery for the user's commit, push, and reply request `#push-reply-plan`

### Build
- [x] Share SQL Lab and chart export behavior `#shared-export`
  - [x] Share authorization, size rejection, and spreadsheet delivery `#shared-export-flow`
  - [x] Consolidate routes and HTTP error mapping; return `spreadsheet_url` `#shared-api-contract`
- [x] Validate chart requests and exception classes `#typed-chart-validation`
  - [x] Add generic MRO-based exception matching and use it in both endpoints `#exception-matching`
  - [x] Parse chart requests at the endpoint boundary into a typed object `#typed-request`
- [x] Bound chart queries and preserve chart tab behavior `#bounded-chart-export`
  - [x] Cap queries without mutating caller input `#bounded-chart-query`
  - [x] Keep tab naming in chart code and test deduplication with the real builder `#chart-tab-names`
  - [x] Match capability reporting to the export permission predicate `#capability-check`
- [x] Publish the reviewed changes and replies to PR #4 `#publish-pr4-review-fixes`
  - [x] Commit and push to PR #4's current head branch `#push-pr4-changes`
  - [x] Reply to each of the ten inline review comments `#reply-pr4-comments`

### Test
- [x] Run the full backend test suite on the pushed commit and register its result `#backend-suite`
- [x] Run mypy, compileall, and committed-diff checks; report unavailable lint tooling `#static-checks`
- [x] Recheck all nine findings and inspect the final diff `#final-review`

### Review
- [ ] Human reviews the pushed PR #4 changes `#human-review`
