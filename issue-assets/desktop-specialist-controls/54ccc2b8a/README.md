# Responsive Specialist controls: review evidence

After source: `54ccc2b8a821660b993c0c67e72938b2e733b917`. Before source: upstream main `492b3f38a008125ab0518abdb7f6a188bb5acd03`. This evidence commit is a direct child of the after source.

All after captures were regenerated from the actual Desktop source at the after SHA. The unchanged before-main frames retain their original hash manifest from evidence `4858b60f2bfa9743e7def516b8cb69f674e57982`. These use deterministic synthetic browser API fixtures for visual inspection, not a live backend/model run. Actual API lifecycle coverage is provided separately by the committed bilingual journey at the same source.

`after-manifest.json` covers the settings row/switch/open Edit/Delete-only menu and narrow layouts. `bound-manifest.json` covers the retained disabled binding at 1440x900 and 960x720 in Chinese and English (DPR 1). Each PNG has source, locale, viewport and SHA-256 recorded.

The responsive driver explicitly opens the seeded bound session, checks the name/Disabled label, then hovers the Expert row. It asserts tooltip opacity 1 and full viewport intersection, including at 960 pixels. It also checks the flyout role is current and natively disabled, with a transparent background even while hovered and muted name colour, while None is not selected. Narrow flyout images document the existing layout too; assertions concern visibility of the current disabled option and full hint, not every sibling panel. Standard flyouts show the complete menu context.

Before the fix, the same 960px hint visibility check failed on source `546520d5d` (Chinese intersection ratio 0.9566), confirming the reported clipping. No product code is changed in this evidence commit.