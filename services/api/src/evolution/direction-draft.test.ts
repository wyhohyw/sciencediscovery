// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License").

import assert from "node:assert/strict";
import { EVOLVE_EDITABLE_LIMIT } from "@sciencediscovery/schema";
import { createTest } from "../../../../test/support/tagged/compat.mjs";
import { directionFromEvolveStart, parseEvolveDirectionDraft } from "./direction-draft.js";

const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "arch:amd64", "arch:arm64"] });

test("draft accepts plain and fenced JSON and keeps only distinct selectable top-level fields", () => {
  const tooLong = "x".repeat(81);
  const baseline = { weight: 0, fixed: { nested: true }, [tooLong]: 1 };
  const json = JSON.stringify({ baseline, editable: ["weight", "weight", "missing", "fixed.nested", tooLong, 7] });
  const expected = { baseline: JSON.stringify(baseline), editable: ["weight"] };
  assert.deepEqual(parseEvolveDirectionDraft(json), expected);
  assert.deepEqual(parseEvolveDirectionDraft("```json\n" + json + "\n```"), expected);
});

test("a packaged evolve start becomes the draft constraints", () => {
  const start = { hotspots: ["A417"], num_designs: 1, diffuser_t: 20, final_step: 15, protenix_n_sample: 1, protenix_use_msa: false, protenix_seeds: "42" };
  const draft = directionFromEvolveStart(JSON.stringify(start));
  assert.deepEqual(draft?.editable, ["hotspots", "num_designs", "diffuser_t", "final_step", "protenix_n_sample", "protenix_use_msa"]);
  assert.equal(JSON.parse(draft?.baseline ?? "{}").protenix_seeds, "42");
  assert.equal(directionFromEvolveStart("{}"), undefined);
  assert.equal(directionFromEvolveStart("[]"), undefined);
});

test("draft rejects unusable replies and caps a valid editable list", () => {
  for (const invalid of ["not JSON", "null", "{}", '{"baseline":[]}', '{"baseline":{}}']) {
    assert.equal(parseEvolveDirectionDraft(invalid), undefined, invalid);
  }
  const names = Array.from({ length: EVOLVE_EDITABLE_LIMIT + 1 }, (_, index) => `field_${index}`);
  const baseline = Object.fromEntries(names.map(name => [name, 0]));
  assert.deepEqual(parseEvolveDirectionDraft(JSON.stringify({ baseline, editable: names })), {
    baseline: JSON.stringify(baseline), editable: names.slice(0, EVOLVE_EDITABLE_LIMIT),
  });
});
