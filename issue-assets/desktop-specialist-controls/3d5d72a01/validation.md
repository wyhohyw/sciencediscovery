# Desktop Specialist controls validation

Tested source: `3d5d72a01925eb60f030ef4b069e9131e7f64a9a`.
Evidence commit is a direct child of that source commit; screenshots are genuine Desktop browser captures.

## Changed behavior

- Custom Specialists support single and bulk enable/disable, persisted by the real API and retained across reload and edits.
- The create button matches the Skills header position and dark pill style.
- Custom rows reuse the Skills chevron menu before the switch: Edit and Delete only, with no Export.
- An explicitly pressed menu survives pointer leave during pane scrolling; hover-only menus remain transient. Escape, outside press, a second trigger press and choosing an action close it.
- Built-in rows retain their enabled switch; the fixed Review Specialist is unchanged.

## Passing verification

| Check | Result |
| --- | --- |
| WSL Desktop/API/Runner build | PASS |
| Desktop typecheck and root test TypeScript check | PASS |
| Desktop unit tests, including Skills export/menu regressions | 1498 passed; 0 failed/skipped |
| Desktop Specialist fixture browser suite | 6 passed; 0 failed/skipped |
| Real-API Desktop config journey, Chinese and English | 2 passed; 0 failed/skipped; all 6 steps each passed |
| E2E metadata | 128 spec files; 0 errors |

The browser journey uses the real compiled public API and Desktop Vite, at 1440x900 and 960x720, DPR 1. It verifies create, disable, refresh persistence, exclusion from the session picker, edit while disabled, transport failure/retry, bulk actions, menu contents, Escape dismissal, narrow layout, cancellation of deletion, and pointer-confirmed deletion followed by an API read. Owned records and services are cleaned up. A loopback stub is registered only for bootstrap, with zero model calls; no live model/research execution is claimed.

Source: [journey](https://github.com/wyhohyw/sciencediscovery/blob/3d5d72a01925eb60f030ef4b069e9131e7f64a9a/test/journey-desktop-specialist-toggle.spec.ts).

## Complete-workspace limitations

The full workspace gate is **not green**. An earlier `pnpm test` stopped at Runner SEA checks (37 pass / 1 fail): the packaged runner cannot resolve `@sciencediscovery/network/src/index.mjs`. The identical error was reproduced on unmodified base main `91242df15` (the standalone spec was 1 pass / 1 fail).

The supplemental recursive workspace run was incomplete and stopped after failures/hanging cases. It included launcher directory-permission failure (also reproduced on base main under the WSL root environment), Runner direct-execution timeouts and executor failures. A streaming-model aggregation case aborted in the broad run; its three baseline cases passed independently. These failures are not treated as passing or silently skipped.

The complete CI UT/ST/E2E layer plans have not been completed on this head. Only the passing checks listed above are claimed. Draft PR review and hosted CI remain necessary. The base branch advanced to `492b3f38a`; a local merge-tree check was clean, but that merge result was not the target of these screenshots/tests.

## Images

- [Header / create action, 1440x900](specialist-header-zh.png)
- [Custom menu with Edit and Delete only, 960x720](specialist-actions-menu-zh.png)
