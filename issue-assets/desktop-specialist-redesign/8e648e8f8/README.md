# Specialist management design evidence

This evidence-only commit is a direct child of source
`8e648e8f80221641e43504e571a73dc1641c8104`. It is kept off the product branch.

The user requested only Specialist management from the 2026-11-26 design
package; Skills/MCP management are references. The source archive SHA-256 is
`8bae486a20e63b83402aa59af22d4eb38f9ab39f207245b6898111af0ea5e7bc`.
The archive and its embedded document scripts are not published here.
Requirements SPUI-01 through SPUI-07 are in the bilingual developer docs.

## Visual comparison

- `before/`: eight native Desktop browser frames from unmodified upstream main
  `3a00fd822e7cd88f0044e7c2e562febb8eb06dd1`.
- `after/`: 22 native Desktop browser frames from the exact source above:
  catalog at 1920×1080, 1440×900 and 960×720; creation, detail, editor,
  lower editor, Skills/MCP drawers, and a 960px editor/drawer in both languages.
- Each directory's `manifest.json` records the source, locale, viewport,
  DPR 1, synthetic fixture and each image's SHA-256. Both versions use the
  same deterministic seven-role catalog, capability data and browser driver.
- `capture.ts` drives the real React Desktop renderer through Playwright.
  These images use a browser API fixture; they are not a live-backend claim
  or generated mockups. Run with the repository's pinned `tsx` and
  Playwright installation: `tsx capture.ts <checkout> <output> [before]`.
  The checkout must have its dependencies/build outputs and `.e2e` installed.

## Actual API journeys

The two `journeys/` reports and their sixteen step screenshots come from the
compiled API/Runner and actual Desktop Vite at the exact source above in a
dedicated WSL stack. Both locales passed all eight steps with no skipped cases
or configured retries. They exercise creation and persisted examples,
drawer cancel/confirm, retained disabled bindings, failed toggle/retry and bulk
changes, real skill deletion and explicit repair, a new bound trial conversation
with prefilled draft and no Run, built-in detail and narrow actions, and
cancelled/confirmed deletion. A loopback model is registered for bootstrap;
zero model calls are asserted. No real-model research workflow is claimed.

## Focused validation at this source

| Check | Result |
| --- | --- |
| Complete `pnpm build`; scoped API/Desktop/Runner build | Passed |
| Desktop and root test TypeScript checks | Passed |
| API store/HTTP Specialist tests | 28 passed, 0 failed, 0 skipped |
| Desktop unit suite | 1,548 passed, 0 failed, 0 skipped |
| Specialist fixture browser suite | 9 passed, 0 failed, 0 skipped, one worker |
| Actual API Desktop journeys | 2 passed, 0 failed, 0 skipped; eight steps each |
| Documentation | 177 files linted, 0 issues; 148 link-checked |
| E2E metadata | 131 spec files, 0 errors |

The first fixture run at this same source used two workers and recorded seven
passes and two 30-second timeouts: the built-in case and partial bulk case
found Settings closed before their next action. Their traces are retained
locally. The complete one-worker rerun passed without changing assertions,
timeouts or source; this does not erase the initial failures or establish their
root cause. Earlier candidates also exposed an incomplete session fixture and
missing deletion confirmation data; both were fixed before this source.

Whole-repository local gates are **not green**: the non-root UT plan recorded
8,494 planned, 8,456 executed, 8,441 passed, 15 failed and 38 not executed;
ST recorded 69 planned/executed, 63 passed and 6 failed. Full mocked E2E first
failed dependency preparation with an npm registry TLS connection reset and
was retried separately. Unmodified-main attribution and hosted CI are recorded
in the PR as they complete. No skipped, aborted or unexecuted check is a pass.
Do not apply #1377's old approvals or CI results to this redesign.
