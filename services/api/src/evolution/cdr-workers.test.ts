// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { createTest } from "../../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "arch:amd64", "arch:arm64"] });
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { verifyCdrWorkerQueues } from "../../../../test/api/cdr-worker-queues.mjs";
import { CdrWorkers, CdrWorkerError, type CdrPlacement, type CdrWorkerClient } from "./cdr-workers.js";

const where: CdrPlacement = {
  runnerId: "lab", cards: [2, 0], workspaceKey: "project/session/runners/lab", workspaceRoot: "/control/workspace",
  epoch: { id: "epoch", environmentRevisionId: "rev", sessionId: "session", createdAt: "2026-01-01", reason: "test", mounts: [], networkPolicy: "none", secretRefs: [] },
};
const owner = { agentId: "evolve", sessionId: "session" };
const structureId = "a".repeat(64);

function fixture() {
  const published = new Map<string, Buffer>(), deleted: string[] = [], uploadOrder: string[] = [];
  let snapshots = 0;
  const content = new Map(["manifest.json", "summary.json", "full.json", "complex.cif"].map(name => [name, Buffer.from(`complete-${name}-中文`)]));
  const prefix = `.evolve/cdr-artifacts/${structureId}/`;
  const client: CdrWorkerClient = {
    getShellExecution: async () => ({ state: "completed" }),
    snapshotRemoteWorkspace: async (workspace, paths) => {
      snapshots++;
      assert.deepEqual(paths, [prefix.slice(0, -1)]);
      return { id: "snapshot", workspace, capturedAt: "now", files: [...content].map(([name, bytes]) => ({
        path: prefix + name, size: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), executable: 0,
      })) };
    },
    streamWorkspaceSnapshot: async (_snapshot, path) => (async function* () {
      const bytes = content.get(path.slice(prefix.length))!;
      for (const byte of bytes) yield Uint8Array.of(byte);
    })(),
    uploadWorkspaceSnapshotFile: async (workspace, path, chunks, metadata, conflict) => {
      assert.equal(workspace, where.workspaceKey);
      assert.equal(conflict, "overwrite");
      const parts = []; for await (const chunk of chunks) parts.push(Buffer.from(chunk));
      const bytes = Buffer.concat(parts);
      assert.equal(bytes.length, metadata.size);
      assert.equal(createHash("sha256").update(bytes).digest("hex"), metadata.sha256);
      published.set(path, bytes); uploadOrder.push(path);
    },
    deleteRemoteWorkspace: async workspace => { deleted.push(workspace); },
  };
  return { client, published, deleted, uploadOrder, prefix, content, snapshots: () => snapshots };
}

test("CDR6-002 slots map to disjoint cards and private non-nested workspaces", () => {
  const pool = new CdrWorkers("run", where, fixture().client);
  const first = pool.bind("one", 0), second = pool.bind("two", 1);
  assert.deepEqual(first.cards, [0]); assert.deepEqual(second.cards, [2]);
  assert.notEqual(first.workspaceKey, second.workspaceKey);
  assert.ok(!first.workspaceKey.startsWith(where.workspaceKey + "/"));
  assert.notEqual(new CdrWorkers("other-run", where, fixture().client).bind("one", 0).workspaceKey, first.workspaceKey);
  for (const slot of [-1, 2, 0.5, "0", true]) assert.throws(() => pool.bind("bad", slot), CdrWorkerError);
  for (const changed of [{ cards: [0] }, { runnerId: "other" }, { epoch: { ...where.epoch, id: "new" } }]) {
    assert.throws(() => pool.assertCurrent({ ...where, ...changed }), /CDR placement changed/);
  }
  pool.assertCurrent({ ...where, cards: [0, 2] });
});

test("CDR6-003 immutable artifacts stream to the primary workspace before worker cleanup", async () => {
  const f = fixture(), pool = new CdrWorkers("run", where, f.client);
  const worker = pool.bind("fold", 1);
  await pool.publish("fold", structureId, owner);
  await pool.publish("fold", structureId, owner);
  assert.equal(f.snapshots(), 1);
  assert.deepEqual([...f.published].sort(), [...f.content].map(([name, bytes]) => [f.prefix + name, bytes]).sort());
  assert.equal(f.uploadOrder.at(-1), f.prefix + "manifest.json");
  await pool.close();
  assert.deepEqual(f.deleted, [worker.workspaceKey]);
  assert.ok(!f.deleted.includes(where.workspaceKey));
  assert.throws(() => pool.bind("late", 0), /run has ended/);
});

test("CDR6-003 publication rejects unbound jobs, traversal, missing and out-of-scope files", async () => {
  const f = fixture(), pool = new CdrWorkers("run", where, f.client);
  pool.bind("fold", 0);
  await assert.rejects(pool.publish("foreign", structureId, owner), /Invalid/);
  await assert.rejects(pool.publish("fold", "../private", owner), /Invalid/);
  f.content.delete("full.json");
  await assert.rejects(pool.publish("fold", structureId, owner), /incomplete/);
  assert.equal(f.published.size, 0);
  await assert.rejects(pool.close(), /retained for inspection/);
  assert.equal(f.deleted.length, 0);
  const g = fixture();
  g.content.set("../../private", Buffer.from("outside"));
  const other = new CdrWorkers("run", where, g.client); other.bind("fold", 0);
  await assert.rejects(other.publish("fold", structureId, owner), /incomplete/);
  assert.equal(g.published.size, 0);
});

test("CDR6-003 cleanup retains workspaces when termination is unverified", async () => {
  for (const job of [{ state: "running" }, { state: "unknown" }, { state: "cancelled" },
    { state: "failed", evidence: { processTerminated: false } }]) {
    const f = fixture(); f.client.getShellExecution = async () => job;
    const pool = new CdrWorkers("run", where, f.client); pool.bind("fold", 0);
    await assert.rejects(pool.close(), /retained for inspection/);
    assert.equal(f.deleted.length, 0);
  }
});

test("CDR6-004 closing a run cancels an active worker before deleting its workspace", async () => {
  const f = fixture(); let cancelled = false;
  f.client.getShellExecution = async () => cancelled
    ? { state: "cancelled", evidence: { processTerminated: true } } : { state: "running" };
  f.client.cancelShellExecution = async () => { cancelled = true; };
  const pool = new CdrWorkers("run", where, f.client); pool.bind("fold", 0);
  await pool.close();
  assert.equal(cancelled, true); assert.equal(f.deleted.length, 1);
});

test("CDR6-004 a newer completed job cannot hide an older unverified worker", async () => {
  const f = fixture();
  f.client.getShellExecution = async id => ({ state: id === "old" ? "unknown" : "completed" });
  const pool = new CdrWorkers("run", where, f.client);
  pool.bind("old", 0); pool.bind("new", 0); pool.observe("new", { state: "completed" });
  await assert.rejects(pool.close(), /retained for inspection/);
  assert.equal(f.deleted.length, 0);
});

test("CDR6-006 actual Runner workspace queues admit both slots concurrently", async () => {
  const pool = new CdrWorkers("run", where, fixture().client);
  await verifyCdrWorkerQueues((id, slot) => pool.bind(id, slot), where.epoch);
});
