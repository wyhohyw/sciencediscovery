// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import assert from "node:assert/strict";
import { createTest } from "../../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "os:windows", "os:macos", "arch:amd64", "arch:arm64"] });
import { DatabaseSync } from "node:sqlite";
import { createMcpWorkspaceTools, type McpWorkspaceToolOptions } from "@sciencediscovery/artifact-manager";
import { createMcpSourceRegistry, createPublicBiomedSources } from "@sciencediscovery/mcp-sources";
import type { McpSourceCatalog } from "@sciencediscovery/data-source";
import type { SessionRun } from "@sciencediscovery/schema";
import type { SessionStore } from "../store.js";
import { ConversationalConfigurationFramework } from "./framework.js";
import { prepareNativeConnectorActivation, verifyNativeConfigurationToolAssembly } from "./native-activation.js";
import { ConfigurationRepository } from "./repository.js";
import type { ConfigurationApplyReceipt, ConfigurationRecord } from "./types.js";

function fixture() {
  const database = new DatabaseSync(":memory:"); const repository = new ConfigurationRepository(database);
  const registry = createMcpSourceRegistry();
  for (const adapter of createPublicBiomedSources(["pubmed", "arxiv"])) registry.register(adapter);
  const catalog = { getStatus: (id: string) => ({ status: "ready", availableTools: Object.keys(registry.get(id).manifest.tools) }) } as unknown as McpSourceCatalog;
  let generation = 7;
  const run = { id: "run-1", sessionId: "session-1", status: "blocked", configurationWait: true,
    configurationCheckpoint: { requestId: "request-1", stopGeneration: 7 },
    settingsSnapshot: { enabledConnectorIds: ["arxiv"], plugins: {} } } as unknown as SessionRun;
  const record: ConfigurationRecord = { view: { id: "request-1", sessionId: "session-1", projectId: "project-1", runId: "run-1",
    revision: 1, stateVersion: 1, adapterVersion: 1, schemaVersion: 1, capabilityId: "connector.enable", state: "applied", reason: { code: "required", message: "PubMed needed" },
    scope: { kind: "session", id: "session-1" }, fields: [], changes: [], impact: { resourceLocation: "session", effectiveScope: "session", message: "approved" },
    verificationPlan: { targets: [], actions: [], maxRequests: 0, mayCharge: false }, previewDigest: "approved-digest",
    activation: { mode: "same_run", state: "pending" }, resumption: { state: "pending", runId: "run-1" },
    allowedActions: [], createdAt: "2026-10-19T00:00:00.000Z", updatedAt: "2026-10-19T00:00:00.000Z" },
    adapterVersion: 1, schemaVersion: 1, input: { connectorId: "pubmed" }, target: "pubmed", fingerprint: "request-fingerprint",
    plan: { fields: [], changes: [], impact: { resourceLocation: "session", effectiveScope: "session", message: "approved" }, verificationPlan: { targets: [], actions: [], maxRequests: 0, mayCharge: false }, activationMode: "same_run", ready: true },
    resourceVersions: { settings: "saved-1" }, stopGeneration: 7, approvedRevision: 1, approvedDigest: "approved-digest", createdBy: "test" };
  const receipt: ConfigurationApplyReceipt = { id: "receipt-1", requestId: "request-1", sessionId: "session-1", revision: 1,
    previewDigest: "approved-digest", capabilityId: "connector.enable", stopGeneration: 7, result: {}, resourceVersions: { settings: "saved-1" }, createdAt: "2026-10-19T00:00:00.000Z" };
  const fakeStore = { getConfigurationDatabase: () => database,
    configurationSnapshot: () => ({ stopGeneration: generation, resourceVersions: { settings: "saved-1" } }),
    notifications: { generation: () => generation, canWake: () => generation === 7 },
    getSessionRun: async () => structuredClone(run), runConfigurationTransaction: async (_: string, action: () => unknown) => repository.transaction(action),
    getSession: () => ({ id: "session-1" }), listProjects: () => [], listSessions: () => [],
  } as unknown as SessionStore;
  repository.save(record); repository.saveReceipt(receipt);
  repository.enqueueOutbox({ id: "request-1:1:activate", requestId: "request-1", revision: 1, sessionId: "session-1", kind: "activate", state: "pending", payload: {}, createdAt: "now", updatedAt: "now" });
  const tools = () => createMcpWorkspaceTools({ registry, catalog, enabledSourceIds: ["arxiv", "pubmed"] } as unknown as McpWorkspaceToolOptions).mcpTools!;
  const verify = () => verifyNativeConfigurationToolAssembly({ store: fakeStore, sessionId: "session-1", runId: "run-1", requestId: "request-1", enabledConnectorIds: ["arxiv", "pubmed"], tools: tools() });
  const prepare = () => prepareNativeConnectorActivation({ store: fakeStore, record: repository.get("request-1")!, receipt, registry, catalog });
  const framework = Object.assign(Object.create(ConversationalConfigurationFramework.prototype) as ConversationalConfigurationFramework, {
    store: fakeStore, repository, credentials: { sweep: () => {} }, phaseQueues: new Map(),
    service: { getRecord: (_: string, id: string) => repository.get(id)!, get: (_: string, id: string) => repository.get(id)!.view },
    options: { nativeExecutor: true, connector: () => ({ name: "PubMed", ready: true }), activate: prepare },
  }) as unknown as ConversationalConfigurationFramework;
  return { database, repository, registry, catalog, record, receipt, store: fakeStore, run, tools, verify, prepare, framework,
    stop: () => { generation++; } };
}

test("CFG-T28 actual Native workspace tool factory matches the persisted activation witness", async () => {
  const f = fixture(); await f.prepare();
  const actual = f.tools(); assert.ok(actual.some(tool => tool.sourceId === "pubmed" && typeof tool.execute === "function"));
  assert.doesNotThrow(f.verify);
  const row = f.database.prepare("SELECT verified_at FROM configuration_native_activation_v2").get() as { verified_at: string };
  assert.ok(row.verified_at); f.database.close();
});

test("CFG-T28 changed tool schema or unapproved connector selection cannot pass resumed assembly", async () => {
  const f = fixture(); await f.prepare();
  const tools = f.tools(); tools[0]!.inputSchema = { type: "object", additionalProperties: false, properties: { changed: { type: "string" } } };
  assert.throws(() => verifyNativeConfigurationToolAssembly({ store: f.store, sessionId: "session-1", runId: "run-1", requestId: "request-1", enabledConnectorIds: ["arxiv", "pubmed"], tools }), /does not match/);
  assert.throws(() => verifyNativeConfigurationToolAssembly({ store: f.store, sessionId: "session-1", runId: "run-1", requestId: "request-1", enabledConnectorIds: ["arxiv", "pubmed", "unapproved"], tools: f.tools() }), /does not match/);
  f.database.close();
});

test("CFG-T28 missing prepared witness, disabled frozen plugin and Stop all fail closed", async () => {
  const f = fixture(); assert.throws(f.verify, /no prepared/);
  f.run.settingsSnapshot.plugins = { "connector.pubmed": { enabled: false } };
  await assert.rejects(f.prepare(), /frozen run policy/);
  f.run.settingsSnapshot.plugins = {}; await f.prepare(); f.stop(); assert.throws(f.verify, /does not match/); f.database.close();
});

test("CFG-T28 concurrent settle calls assemble once and independently retry failed resume outbox", async () => {
  const f = fixture(); let activations = 0, resumes = 0, fail = true;
  f.framework.options.activate = async () => { activations++; return f.prepare(); };
  f.framework.options.resume = async () => {
    resumes++; if (fail) throw new Error("queue temporarily unavailable");
    f.run.status = "queued"; f.run.configurationWait = false;
    f.run.configurationResume = { requestId: "request-1", decision: "apply", connectorName: "PubMed" };
    return structuredClone(f.run);
  };
  await f.framework.settle("session-1", "request-1");
  assert.equal(f.repository.get("request-1")!.view.activation.state, "active");
  assert.equal(f.repository.get("request-1")!.view.resumption.state, "failed");
  assert.equal(f.repository.getOutbox("request-1:1:activate")!.state, "done");
  fail = false; await Promise.all([f.framework.settle("session-1", "request-1"), f.framework.settle("session-1", "request-1")]);
  assert.equal(activations, 1); assert.equal(resumes, 2);
  assert.equal(f.repository.getOutbox("request-1:1:resume")!.state, "done"); f.database.close();
});

test("CFG-T27 crash after queue commit reconciles original run without invoking resume again", async () => {
  const f = fixture(); let resumes = 0;
  f.framework.options.resume = async () => { resumes++; throw new Error("queue failed before commit"); };
  await f.framework.settle("session-1", "request-1");
  f.run.status = "queued"; f.run.configurationWait = false;
  f.run.configurationResume = { requestId: "request-1", decision: "apply", connectorName: "PubMed" };
  const pending = f.repository.getOutbox("request-1:1:resume")!;
  pending.state = "claimed"; pending.payload.leaseUntil = Date.now() - 1; pending.payload.leaseToken = "crashed-worker";
  f.repository.updateOutbox(pending);
  await f.framework.tick();
  assert.equal(resumes, 1); assert.equal(f.repository.get("request-1")!.view.resumption.state, "queued");
  assert.equal(f.repository.getOutbox("request-1:1:resume")!.state, "done"); f.database.close();
});

test("CFG-T28 Stop after activation failure suppresses saved work without changing configuration receipt", async () => {
  const f = fixture(); f.framework.options.activate = async () => { throw new Error("not ready"); };
  await f.framework.settle("session-1", "request-1"); f.stop(); await f.framework.tick();
  assert.equal(f.repository.get("request-1")!.view.state, "applied");
  assert.equal(f.repository.get("request-1")!.view.resumption.state, "suppressed");
  assert.equal(f.repository.getReceipt("request-1")!.id, "receipt-1");
  assert.equal(f.repository.getOutbox("request-1:1:activate")!.state, "suppressed"); f.database.close();
});
