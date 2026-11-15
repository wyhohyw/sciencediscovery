import assert from "node:assert/strict";
import test from "node:test";

import { designDigest } from "./design-artifacts.js";

test("the design digest matches the evaluator's run directory", () => {
  // python: hashlib.sha256(json.dumps(..., sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]
  const code = JSON.stringify({ b: 1, a: ["x", { z: true, y: "42" }] });
  assert.equal(designDigest(code), "531881c00993");
  assert.equal(designDigest("not json"), undefined);
  assert.equal(designDigest("[1]"), undefined);
});
