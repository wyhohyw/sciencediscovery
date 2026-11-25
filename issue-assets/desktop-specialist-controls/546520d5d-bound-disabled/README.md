# Disabled current binding: Desktop visual evidence

Source: `546520d5d974ad1ff9df6cbfe2a39a1cbb84767e`. This evidence commit is a direct child of that source.

The four images show the actual Desktop app at 1440x900, DPR 1, with deterministic synthetic browser API data. They are not live backend/model evidence. Each locale has a session-menu screenshot with the disabled role name and visible retained-binding hint, and a flyout screenshot with the current disabled role selected.

The driver asserts the disabled name and hint, opacity 1 and full viewport visibility of the hint, `aria-current=true` and the native disabled state of the selected role, and that None is not selected. It uses the pinned Playwright/Chromium installed in the isolated runtime. `after-manifest.json` records source, locale, state, viewport and SHA-256 of every PNG. The real-API bilingual journey on this same source separately validates disabling a bound role, retaining the binding, and clearing it via None.