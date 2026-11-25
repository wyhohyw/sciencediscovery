# PR #1377 review evidence

Review addressed: HongyeZhou comment 7152087082.
Before: upstream main commit `492b3f38a008125ab0518abdb7f6a188bb5acd03`.
After: PR commit `4a968a15af4bdd0a0e9afe46989c14331d4a2cc4`.
This evidence commit is a direct child of the after commit.

## What the images establish

These are native Chromium screenshots of the actual `apps/desktop` code at each immutable ref, with the same deterministic browser API fixture and synthetic records. They are UI comparisons, not fabricated mockup images or live-backend execution claims. Chinese and English each show the full custom row before/after. After images include its enabled blue switch and the open Edit/Delete-only menu. Additional 960x720 images cover the narrow window.

The before UI has inline Edit/Delete controls and no custom-role switch or menu. It cannot show an open menu because that control does not exist in the baseline. Full context frames are retained beside the closeups.

Both runtimes used the repository-pinned Playwright installation and Chromium, DPR 1. The manifests record source commit, locale, viewport and SHA-256 for every PNG. The capture driver is included. Run it from a prepared worktree with pinned `.e2e` dependencies: `node --import tsx capture-specialist-comparison.ts <runtime> <output> before|after <port>`. It owns its Vite process and browser; it makes no model or live backend calls.

## Scope of the follow-up commit

`4a968a15a` changes only `docs/en/developer-docs/desktop-frontend.md`. Runtime/UI code is identical to tested `3d5d72a01925eb60f030ef4b069e9131e7f64a9a`. The wording now promises omission from the session picker and generated subagent catalog, while acknowledging the existing explicit-ID resolution path.

`pnpm docs:check` on `4a968a15a`: Markdown lint 172 files, 0 issues; documentation links passed, 143 Markdown files checked. The earlier runtime checks remain attributable to `3d5d72a0`: 1498 Desktop unit tests, 6 fixture browser cases, and 2 real-API Desktop configuration journeys all passed with zero failures/skips. The latter drive actual API persistence, edit while disabled, cancelled deletion and pointer-confirmed deletion. No live model research-chain execution is claimed.

Hosted CI run [45539964857](https://github.com/openJiuwen-ai/sciencediscovery/actions/runs/45539964857) at `3d5d72a0` passed its executed jobs, including L1, UT, ST, Desktop UI-ST, both mocked E2E shards, both Linux binaries, Docker, Coverage and merge-gate. Policy-skipped real E2E and installer jobs are not passes. New-head CI is tracked separately.
