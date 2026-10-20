// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import assert from "node:assert/strict";
import { createTest } from "../../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "os:windows", "arch:amd64", "arch:arm64"] });
import { DatabaseSync } from "node:sqlite";
import { ConfigurationRequestService } from "./request-service.js";
import { ConfigurationRepository } from "./repository.js";
import { ConfigurationRegistry } from "./registry.js";
import type { ConfigurationAdapter, ConfigurationSnapshot } from "./types.js";
import { ConfigurationError, object, string } from "./validation.js";
import { normalizeConfigurationFields, normalizeTokenSchema } from "./schema-normalizer.js";

function harness(options: { verify?: ConfigurationAdapter["verify"]; applyFails?: boolean; credential?: boolean } = {}) {
  const db = new DatabaseSync(":memory:");
  db.exec("CREATE TABLE domain_settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL); INSERT INTO domain_settings VALUES (1, '[]')");
  const repo = new ConfigurationRepository(db);
  let generation = 0; let version = 0; let writes = 0; let writable = true;
  const snapshot = (): ConfigurationSnapshot => ({ projectId: "project", stopGeneration: generation, writable, resourceVersions: { settings: String(version), parent: "v1", credential: "generation-1" }, data: {} });
  const adapter: ConfigurationAdapter = {
    id: "connector.enable", version: 1, schemaVersion: 1, scopes: ["session"], persistence: "local_transaction",
    normalize(input) { const value = object(input, ["connectorId"], "values"); return { connectorId: string(value.connectorId, "connectorId") }; },
    target(input) { return String(input.connectorId); },
    preview(input, context) { return { ready: true, fields: [{ id: "connectorId", label: "Connector", required: true, kind: "text", value: input.connectorId }, ...(options.credential ? [{ id: "apiKey", label: "API key", required: true, kind: "credential" as const, status: "missing" as const }] : [])], changes: [{ field: "connectors", after: String(input.connectorId) }], impact: { resourceLocation: "Catalog", effectiveScope: "Session", message: "Keep other connectors" }, verificationPlan: { targets: [], actions: [], maxRequests: 0, mayCharge: false }, activationMode: context.runId ? "same_run" : "next_run", data: { privateRef: "do-not-project" } }; },
    verify: options.verify,
    apply(context) {
      db.prepare("UPDATE domain_settings SET value = ? WHERE id = 1").run(JSON.stringify(["existing", context.input.connectorId]));
      if (options.applyFails) throw new Error("provider SECRET must never escape");
      writes++; version++; return { resourceRevision: String(version), summary: "Enabled" };
    },
    previewReversal() { return { connectorId: "previous" }; },
  };
  let queue: Promise<unknown> = Promise.resolve();
  const host = { snapshot, runInTransaction<T>(_sessionId: string, action: () => T): Promise<T> {
    const operation = queue.then(() => { const oldVersion = version; const oldWrites = writes; try { return repo.transaction(action); } catch (error) { version = oldVersion; writes = oldWrites; throw error; } });
    queue = operation.then(() => undefined, () => undefined); return operation;
  } };
  const registry = new ConfigurationRegistry().register(adapter);
  const service = new ConfigurationRequestService({ repository: repo, registry, host });
  return { db, repo, service, registry, host, adapter, get writes() { return writes; }, stop() { generation++; }, changeVersion() { version++; }, archive() { writable = false; } };
}
const proposal = { capabilityId: "connector.enable" as const, values: { connectorId: "pubmed" }, runId: "run-1" };

test("CFG-T23: finite schema rejects unknown keys, unsupported types, duplicate keys and mismatched credentials", () => {
  assert.throws(() => normalizeTokenSchema([{ key: "token", label: "Token", type: "text", evil: true }]));
  assert.throws(() => normalizeTokenSchema([{ key: "token", label: "Token", type: "file" }]));
  assert.throws(() => normalizeTokenSchema([{ key: "a", label: "A", type: "text" }, { key: "a", label: "B", type: "text" }]));
  assert.throws(() => normalizeTokenSchema([{ key: "token", label: "Token", type: "text" }], { credentialFields: ["token"] }));
  assert.throws(() => normalizeConfigurationFields([{ id: "token", label: "Token", required: true, kind: "credential", status: "provided", value: "SECRET" }]));
  const fields = normalizeTokenSchema([{ key: "token", label: "Token", type: "password", default: "SECRET", placeholder: "SECRET" }], { credentialFields: ["token"] });
  assert.equal(JSON.stringify(fields).includes("SECRET"), false);
  assert.throws(() => normalizeTokenSchema([{ key: "token", label: "Token", type: "text", help: { label: "Docs", url: "https://trusted.example/?secret=abc" } }], { helpHosts: ["trusted.example"] }));
});

test("CFG-01/02: finite registry, strict domain inputs, run-aware preview, DTO projection and immutable revision", async () => {
  const h = harness();
  assert.throws(() => h.registry.get("arbitrary.config"));
  await assert.rejects(h.service.propose("session", { ...proposal, values: { connectorId: "pubmed", secret: "SECRET" } }));
  const view = await h.service.propose("session", proposal);
  assert.equal(view.activation.mode, "same_run");
  assert.equal(JSON.stringify(view).includes("do-not-project"), false);
  const updated = await h.service.preview("session", view.id, { expectedRevision: 1, values: { connectorId: "pubmed" } });
  assert.equal(updated.revision, 2); assert.notEqual(updated.previewDigest, view.previewDigest);
  assert.equal(h.repo.getRevision(view.id, 1)?.view.previewDigest, view.previewDigest);
  await assert.rejects(h.service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "apply" }), { code: "STALE_CONFIGURATION_PREVIEW" });
  const record = h.service.getRecord("session", view.id); record.input.connectorId = "other";
  assert.throws(() => h.repo.transaction(() => h.repo.save(record, record.view.stateVersion)), { code: "STALE_CONFIGURATION_PREVIEW" });
});

test("CFG-T09/10/28: durable idempotency, one apply per revision, atomic receipt and activation outbox", async () => {
  const h = harness();
  const view = await h.service.propose("session", proposal, "proposal-key");
  assert.deepEqual(await h.service.propose("session", proposal, "proposal-key"), view);
  await assert.rejects(h.service.propose("session", { ...proposal, values: { connectorId: "other" } }, "proposal-key"), { code: "IDEMPOTENCY_MISMATCH" });
  const decision = { revision: view.revision, previewDigest: view.previewDigest!, decision: "apply" as const };
  const results = await Promise.all([h.service.decide("session", view.id, decision, "decision-key"), h.service.decide("session", view.id, decision, "decision-key")]);
  assert.equal(results.some((result) => result.state === "applied"), true); assert.equal(h.writes, 1);
  assert.equal((await h.service.decide("session", view.id, decision)).state, "applied"); assert.equal(h.writes, 1);
  assert.ok(h.repo.getReceipt(view.id, 1)); assert.equal(h.repo.listOutbox().length, 1);
  assert.equal(h.repo.listOutbox()[0]?.payload.runId, "run-1");
  assert.deepEqual(JSON.parse((h.db.prepare("SELECT value FROM domain_settings").get() as { value: string }).value), ["existing", "pubmed"]);
  const restarted = new ConfigurationRequestService({ repository: new ConfigurationRepository(h.db), registry: h.registry, host: { snapshot: () => { throw new Error("No writes on replay"); }, runInTransaction: async (_id, action) => action() } });
  assert.equal((await restarted.decide("session", view.id, decision, "decision-key")).state, "applied");
  assert.equal(h.writes, 1);
});

test("CFG-T10: a stale resource vector cannot overwrite settings, and re-preview invalidates approval", async () => {
  const h = harness(); const view = await h.service.propose("session", proposal); h.changeVersion();
  await assert.rejects(h.service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "apply" }), { code: "RESOURCE_VERSION_CONFLICT" });
  assert.equal(h.writes, 0);
  const updated = await h.service.preview("session", view.id, { expectedRevision: 1, values: proposal.values });
  assert.equal(updated.revision, 2);
  assert.equal((await h.service.decide("session", view.id, { revision: 2, previewDigest: updated.previewDigest!, decision: "apply" })).state, "applied");
});

test("CFG-T12/13: Stop during verification suppresses apply and persists cancellation", async () => {
  let finish!: () => void; let begin!: () => void;
  const started = new Promise<void>((resolve) => { begin = resolve; });
  const verified = new Promise<void>((resolve) => { finish = resolve; });
  const h = harness({ verify: async () => { begin(); await verified; return { ok: true }; } });
  const view = await h.service.propose("session", proposal);
  const pending = h.service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "apply" });
  await started; h.stop(); finish(); const result = await pending;
  assert.equal(result.state, "cancelled"); assert.equal(h.writes, 0); assert.equal(h.repo.listOutbox().length, 0);
});

test("CFG-T08: rejection does not write settings and carries original run resume intent", async () => {
  const h = harness(); const view = await h.service.propose("session", proposal);
  const result = await h.service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "dismiss" });
  assert.equal(result.state, "dismissed"); assert.equal(h.writes, 0); assert.equal(h.repo.listOutbox()[0]?.kind, "resume");
  assert.equal(h.repo.listOutbox()[0]?.payload.decision, "dismiss");
});

test("CFG-T06/23: credential availability changes the approved revision without exposing handle", async () => {
  const h = harness({ credential: true }); const view = await h.service.propose("session", proposal);
  assert.equal(view.state, "collecting_input");
  const updated = await h.service.preview("session", view.id, { expectedRevision: 1, values: proposal.values, credentialHandle: "private-handle" });
  assert.equal(updated.state, "awaiting_confirmation"); assert.equal(JSON.stringify(updated).includes("private-handle"), false);
  assert.equal(updated.fields.find((field) => field.kind === "credential")?.status, "provided");
  await assert.rejects(h.service.preview("session", view.id, { expectedRevision: 2, values: { connectorId: "different-target" }, credentialHandle: "private-handle" }), { code: "INVALID_CONFIGURATION_INPUT" });
  const changed = await h.service.preview("session", view.id, { expectedRevision: 2, values: { connectorId: "different-target" } });
  assert.equal(changed.state, "collecting_input");
  assert.equal(h.service.getRecord("session", view.id).credentialHandle, undefined);
});

test("CFG-T14: reversal is a new proposal and refuses resource changes after original receipt", async () => {
  const h = harness(); const view = await h.service.propose("session", proposal);
  await h.service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "apply" });
  const reversal = await h.service.reversal("session", view.id); assert.notEqual(reversal.id, view.id); assert.equal(reversal.state, "awaiting_confirmation");
  h.changeVersion(); await assert.rejects(h.service.reversal("session", view.id), { code: "RESOURCE_VERSION_CONFLICT" });
});

test("CFG-02: synchronous transaction guard rolls back domain settings on failure", async () => {
  const h = harness({ applyFails: true }); const view = await h.service.propose("session", proposal);
  const failed = await h.service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "apply" });
  assert.equal(failed.state, "failed"); assert.equal(JSON.stringify(failed).includes("SECRET"), false);
  assert.equal((h.db.prepare("SELECT value FROM domain_settings").get() as { value: string }).value, "[]");
  assert.equal(h.repo.getReceipt(view.id), undefined); assert.equal(h.repo.listOutbox().length, 0);
  const second = await h.service.propose("session", { ...proposal, values: { connectorId: "other" } }); assert.equal(second.state, "awaiting_confirmation");
  assert.throws(() => h.repo.transaction(() => Promise.resolve()), /synchronous/);
});

test("CFG-T08/10: dismissal remains available after another settings entry changes the baseline", async () => {
  const h = harness(); const view = await h.service.propose("session", proposal); h.changeVersion();
  const result = await h.service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "dismiss" });
  assert.equal(result.state, "dismissed"); assert.equal(h.writes, 0);
});

test("CFG-T06: nested DTO extensions cannot leak private server references", async () => {
  const h = harness(); const view = await h.service.propose("session", proposal);
  const record = h.service.getRecord("session", view.id);
  Object.assign(record.view.reason, { credentialHandle: "SECRET" });
  Object.assign(record.view.resumption, { secretRef: "SECRET" });
  Object.assign(record.view.activation, { backendReceipt: "SECRET" });
  record.view.autoApplyAt = new Date(Date.now() + 30_000).toISOString();
  record.view.decisionSource = "auto";
  record.view.stateVersion++;
  h.repo.transaction(() => h.repo.save(record, view.stateVersion));
  const projected = h.service.get("session", view.id);
  assert.equal(JSON.stringify(projected).includes("SECRET"), false);
  assert.equal(projected.autoApplyAt, record.view.autoApplyAt);
  assert.equal(projected.decisionSource, "auto");
  const version = record.view.stateVersion;
  delete record.view.autoApplyAt; record.view.stateVersion++;
  h.repo.transaction(() => h.repo.save(record, version));
  assert.equal(h.service.get("session", view.id).autoApplyAt, undefined);
  assert.equal(h.service.get("session", view.id).previewDigest, view.previewDigest);
});

test("CFG-T24/27: startup classifies an abandoned verification as unknown without sending another probe", async () => {
  let probes = 0;
  const h = harness({ verify: async () => { probes++; return { ok: true }; } });
  const view = await h.service.propose("session", proposal);
  h.repo.transaction(() => {
    const record = h.service.getRecord("session", view.id); record.view.state = "validating";
    record.approvedRevision = 1; record.approvedDigest = view.previewDigest; record.view.stateVersion++;
    h.repo.save(record, view.stateVersion);
    h.repo.saveVerificationAttempt({ id: `${view.id}:1:verify`, requestId: view.id, sessionId: "session", revision: 1, ownerId: "exited-process", state: "started", startedAt: new Date().toISOString() });
    h.repo.reserveIdempotency("session:pending", "pending-key", { approved: true }, view.id);
  });
  const restarted = new ConfigurationRequestService({ repository: h.repo, registry: h.registry, host: h.host });
  assert.equal(await restarted.recoverPendingVerifications(), 1);
  assert.equal(await restarted.recoverPendingVerifications(), 0);
  const recovered = restarted.get("session", view.id);
  assert.equal(recovered.state, "needs_fix"); assert.equal(recovered.error?.code, "VERIFICATION_RESULT_UNKNOWN");
  assert.equal(h.repo.getVerificationAttempt(`${view.id}:1:verify`)?.state, "unknown");
  assert.equal(probes, 0); assert.equal(h.writes, 0); assert.equal(h.repo.listOutbox().length, 0);
  await assert.rejects(restarted.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "apply" }), { code: "INVALID_CONFIGURATION_INPUT" });
  const fresh = await restarted.preview("session", view.id, { expectedRevision: 1, values: proposal.values });
  assert.equal((await restarted.decide("session", view.id, { revision: fresh.revision, previewDigest: fresh.previewDigest!, decision: "apply" })).state, "applied");
  assert.equal(probes, 1);
});

test("CFG-T24: external writes enqueue approved operations and never claim a local application receipt", async () => {
  const h = harness();
  const registry = new ConfigurationRegistry().register({ ...h.adapter, persistence: "external_control", apply: undefined });
  const service = new ConfigurationRequestService({ repository: h.repo, registry, host: h.host });
  const view = await service.propose("session", proposal);
  const result = await service.decide("session", view.id, { revision: 1, previewDigest: view.previewDigest!, decision: "apply" });
  assert.equal(result.state, "applying"); assert.equal(h.writes, 0); assert.equal(h.repo.getReceipt(view.id), undefined);
  assert.equal(h.repo.listOutbox()[0]?.kind, "external_submit");
  assert.equal(result.activation.state, "not_requested");
});

test("CFG-T06/23: credential rebind callback shares the revision transaction", async () => {
  const h = harness({ credential: true }); const view = await h.service.propose("session", proposal);
  const service = new ConfigurationRequestService({ repository: h.repo, registry: h.registry, host: { ...h.host,
    onPreviewRevision(input) {
      assert.equal(h.db.isTransaction, true); assert.equal(input.fromRevision, 1); assert.equal(input.toRevision, 2);
      h.db.prepare("UPDATE domain_settings SET value='temporary'").run();
      throw new ConfigurationError("CREDENTIAL_DRAFT_EXPIRED", "Re-enter the credential", 410);
    },
  } });
  await assert.rejects(service.preview("session", view.id, { expectedRevision: 1, values: proposal.values, credentialHandle: "opaque-handle" }), { code: "CREDENTIAL_DRAFT_EXPIRED" });
  assert.equal(service.get("session", view.id).revision, 1);
  assert.equal(h.repo.getRevision(view.id, 2), undefined);
  assert.equal((h.db.prepare("SELECT value FROM domain_settings").get() as { value: string }).value, "[]");
});
