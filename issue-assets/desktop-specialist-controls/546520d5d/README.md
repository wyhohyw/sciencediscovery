# Desktop Specialist controls review evidence

After source: `546520d5d974ad1ff9df6cbfe2a39a1cbb84767e`.
Before source: upstream main `492b3f38a008125ab0518abdb7f6a188bb5acd03`.

These Chromium screenshots render the actual Desktop UI with the same deterministic browser API fixture in Chinese and English. They do not claim a live model run. `before-*` frames and their SHA-256 manifest are retained unchanged from the accepted comparison batch at evidence commit `4858b60f2bfa9743e7def516b8cb69f674e57982`. All `after-*` frames were recaptured at the new source commit. Manifests list source, locale, viewport, DPR and hashes.

The capture driver asserts no custom switch in before; after has an enabled switch and exactly Edit/Delete in its open menu. Both menu items and switch must be fully inside the narrow 960x720 viewport. Standard viewport is 1440x900, DPR 1. Real API lifecycle and disabled-binding regressions are validated separately by the committed bilingual Desktop journey.

This evidence commit has the after source as its direct parent. No product source changes are included here.