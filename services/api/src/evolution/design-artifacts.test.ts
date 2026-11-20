import assert from "node:assert/strict";
import test from "node:test";

import { designDigest, evolveSummaryMarkdown } from "./design-artifacts.js";

test("the design digest matches the evaluator's run directory", () => {
  // python: hashlib.sha256(json.dumps(..., sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
  const code = JSON.stringify({ b: 1, a: ["x", { z: true, y: "42" }] });
  assert.equal(designDigest(code), "531881c00993");
  assert.equal(designDigest("not json"), undefined);
  assert.equal(designDigest("[1]"), undefined);
});

test("the evolve summary names each recorded change and the best node", () => {
  const markdown = evolveSummaryMarkdown([
    { changeSummary: "num_designs 1→10", nodeIndex: 2, score: 0 },
    { changeSummary: "protenix_use_msa false→true", nodeIndex: 1, score: 0.061 },
  ], 1);
  assert.match(markdown, /节点 1：protenix_use_msa false→true，分数 0\.061/);
  assert.match(markdown, /节点 2：num_designs 1→10，分数 0/);
  assert.match(markdown, /最好的节点是 1，分数 0\.061/);
  assert.match(evolveSummaryMarkdown([]), /没有留下可记录的改动/);
});
