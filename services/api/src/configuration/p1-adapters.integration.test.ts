// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { rm } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { createTest } from "../../../../test/support/tagged/compat.mjs";
import type { ConfigurationRequestView, SkillLibrary } from "@sciencediscovery/schema";
import { SessionStore } from "../store.js";
import { configurationFramework, type ConversationalConfigurationFramework } from "./framework.js";
import { createConfigurationAgentTools } from "./agent-tools.js";
import { configurationDiagnostic } from "./diagnostics.js";
import { normalizeConfigurationFields, normalizeConfigurationValues } from "./schema-normalizer.js";
import { ReviewerAuditCoordinator } from "../reviewer-specialist/audit-coordinator.js";
import { recordNativeRunChoiceContext } from "./native-choices.js";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "os:windows", "arch:amd64", "arch:arm64"] });
async function harness(context: { after(action: () => Promise<void>): void }) {
  const parent = resolve(process.cwd(), ".tmp"); const root = resolve(parent, `configuration-p1-${randomUUID()}`);
  const store = new SessionStore(root); await store.load();
  const cleanup: Array<() => Promise<unknown>> = [];
  context.after(async () => { for (const action of cleanup) await action(); store.close(); assert.ok(root.startsWith(`${parent}${sep}`)); await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 20 }); });
  store.setAvailableSkillIds(["literature", "statistics"]);
  const libraries: SkillLibrary[] = [{ id: "methods", name: "Methods library", headVersionId: "version-1", createdAt: "2026-01-01", updatedAt: "2026-01-01" }];
  store.setConfigurationSkillLibrarySource(() => libraries);
  const project = await store.createProject("P1 scope test");
  await store.replaceProjectSettings(project.id, { skillSelectionMode: "all" });
  const session = await store.createSession(project.id, "Target", {}, {}, { allowUnconfiguredModel: true });
  const other = await store.createSession(project.id, "Other", {}, {}, { allowUnconfiguredModel: true });
  const framework = configurationFramework(store, { nativeExecutor: true, connector: () => ({ name: "Installed", ready: true }) });
  return { store, project, session, other, framework, libraries, cleanup };
}
async function approve(framework: ConversationalConfigurationFramework, view: ConfigurationRequestView) {
  const preview = view.protocolVersion === 3 ? await framework.preview(view.sessionId, view.id,
    { expectedRevision: view.revision, values: framework.service.getRecord(view.sessionId, view.id).input, selectedOptionId: "persist" }) : view;
  return framework.decide(view.sessionId, view.id, { revision: preview.revision, previewDigest: preview.previewDigest!, decision: "apply", ...(preview.protocolVersion === 3 ? { selectedOptionId: "persist" } : {}) });
}
test("CFG-T29/33: registry generates all registered Agent parameters and rejects fields belonging to another domain", async context => {
  const h = await harness(context); const registry = h.framework.service.registry;
  const parameters = JSON.stringify(registry.proposalParameters());
  for (const item of registry.describe()) { assert.ok(parameters.includes(item.id)); for (const field of Object.keys(item.fields)) assert.ok(parameters.includes(field)); }
  assert.equal(registry.describe().length, 7);
  assert.throws(() => registry.agentValues("model.select", { enabledSkillIds: ["literature"] }));
  assert.throws(() => registry.agentValues("thinking.configure", { command: "install" }));
});
test("CFG-T33: finite ID multiselect rejects CSV, unknown IDs, duplicates and credential projection", () => {
  const fields = normalizeConfigurationFields([{ id: "skills", label: "Skills", required: false, kind: "multiselect", choices: [{ id: "a", label: "A" }], value: ["a"] }]);
  assert.deepEqual(normalizeConfigurationValues({ skills: [] }, fields), { skills: [] });
  for (const skills of ["a", ["unknown"], ["a", "a"], [4], { a: true }]) assert.throws(() => normalizeConfigurationValues({ skills }, fields));
  assert.throws(() => normalizeConfigurationFields([{ id: "key", label: "Key", kind: "credential", required: true, status: "provided", value: ["secret"] }]));
});
test("CFG-T33: Project Skill whitelist and versioned library selection reuse formal inheritance; existing Session overrides survive", async context => {
  const h = await harness(context);
  await h.store.replaceSessionSettings(h.other.id, { skillSelectionMode: "selected", enabledSkillIds: ["statistics"] });
  const frozen = structuredClone(h.store.resolveRuntimeSettings(h.session.id).effective);
  const queued = await h.store.createSessionRun({ sessionId: h.session.id, prompt: "Existing task", settingsSnapshot: frozen });
  const view = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "skill.select", values: {
    skillSelectionMode: "selected", enabledSkillIds: ["literature"], enabledSkillLibraryIds: ["methods"],
  }, scope: { kind: "project", id: h.project.id } });
  assert.equal(view.activation.mode, "next_run"); await approve(h.framework, view);
  assert.deepEqual(h.store.getProjectSettings(h.project.id).overrides.enabledSkillLibraries, [{ libraryId: "methods", versionId: "version-1" }]);
  assert.deepEqual(h.store.resolveRuntimeSettings(h.session.id).effective.enabledSkillIds, ["literature"]);
  assert.equal(h.store.getSessionSettings(h.session.id).sources.enabledSkillIds, "project");
  assert.deepEqual(h.store.resolveRuntimeSettings(h.other.id).effective.enabledSkillIds, ["statistics"]);
  assert.deepEqual((await h.store.getSessionRun(h.session.id, queued.id))?.settingsSnapshot, queued.settingsSnapshot);
});
test("CFG-T33: installed-only selections fail closed and do not modify settings", async context => {
  const h = await harness(context); const before = h.store.getSessionSettings(h.session.id);
  for (const values of [{ enabledSkillIds: ["not-installed"], skillSelectionMode: "selected" }, { enabledSkillLibraryIds: ["not-installed"] }, { enabledSkillIds: "literature" }]) {
    await assert.rejects(h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "skill.select", values } as never));
  }
  assert.deepEqual(h.store.getSessionSettings(h.session.id), before);
});
test("CFG-T33: a changed library head invalidates approval, then a refreshed preview pins the approved head", async context => {
  const h = await harness(context);
  const old = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "skill.select", values: { enabledSkillLibraryIds: ["methods"] } });
  const approvedOld = await h.framework.preview(h.session.id, old.id, { expectedRevision: old.revision, values: { enabledSkillLibraryIds: ["methods"] }, selectedOptionId: "persist" });
  h.libraries[0]!.headVersionId = "version-2";
  await assert.rejects(h.framework.decide(h.session.id, old.id, { revision: approvedOld.revision, previewDigest: approvedOld.previewDigest!, selectedOptionId: "persist", decision: "apply" }), { code: "RESOURCE_VERSION_CONFLICT" });
  assert.deepEqual(h.store.getSessionSettings(h.session.id).effective.enabledSkillLibraries, []);
  const fresh = await h.framework.preview(h.session.id, old.id, { expectedRevision: approvedOld.revision, values: { enabledSkillLibraryIds: ["methods"] }, selectedOptionId: "persist" });
  await approve(h.framework, fresh);
  assert.equal(h.store.getSessionSettings(h.session.id).overrides.enabledSkillLibraries?.[0]?.versionId, "version-2");
});
test("CFG-T33: selecting an inherited pinned library preserves its approved version instead of silently upgrading to the current head", async context => {
  const h = await harness(context);
  await h.store.replaceProjectSettings(h.project.id, { skillSelectionMode: "all", enabledSkillLibraries: [{ libraryId: "methods", versionId: "version-1", priority: 2, limit: 5 }] });
  h.libraries[0]!.headVersionId = "version-2";
  const view = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "skill.select", values: { enabledSkillLibraryIds: ["methods"] } });
  assert.match(view.changes[0]!.after!, /version-1/); assert.ok(!view.changes[0]!.after!.includes("version-2"));
  await approve(h.framework, view);
  assert.deepEqual(h.store.getSessionSettings(h.session.id).overrides.enabledSkillLibraries, [{ libraryId: "methods", versionId: "version-1", priority: 2, limit: 5 }]);
});
test("CFG-T33/34: thinking requires real variant controls and legal model-specific efforts; saved settings never hot-swap a queued run", async context => {
  const h = await harness(context);
  await h.store.createModel({ name: "Plain API", model: "plain", baseUrl: "https://example.test/v1", apiToken: "never-exposed" });
  await assert.rejects(h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "thinking.configure", values: { thinkingMode: "enabled" } }), { code: "THINKING_CONTROL_UNSUPPORTED" });
  const model = await h.store.createModel({ name: "Reasoning API", model: "reasoning", baseUrl: "https://example.test/v1", apiProtocol: "openai-responses", apiVariant: "responses", apiToken: "never-exposed", facts: { thinkingEfforts: ["low", "high"] } });
  await h.store.updateSession(h.session.id, { modelId: model.id });
  const frozen = h.store.resolveRuntimeSettings(h.session.id).effective;
  const queued = await h.store.createSessionRun({ sessionId: h.session.id, prompt: "Already queued", settingsSnapshot: frozen });
  await assert.rejects(h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "thinking.configure", values: { thinkingEffort: "max" } }));
  const view = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "thinking.configure", values: { thinkingMode: "enabled", thinkingEffort: "low" } });
  assert.equal(view.verificationPlan.maxRequests, 0); await approve(h.framework, view);
  assert.equal(h.store.getSessionSettings(h.session.id).effective.thinkingEffort, "low");
  assert.equal(h.store.getSessionSettings(h.other.id).overrides.thinkingEffort, undefined);
  assert.deepEqual((await h.store.getSessionRun(h.session.id, queued.id))?.settingsSnapshot, queued.settingsSnapshot);
  assert.notEqual(h.store.getModel(model.id)?.thinkingEffort, "low");
});
test("CFG-T33: session review writes the same formal runtime and reviewer endpoints without enabling the global Specialist", async context => {
  const h = await harness(context);
  const model = await h.store.createModel({ name: "Review API", model: "review", baseUrl: "https://example.test/v1", apiToken: "never-exposed" });
  const globalBefore = h.store.getReviewerSpecialistSettings();
  const otherBefore = h.store.getSessionReviewerSpecialistSettings(h.other.id);
  const view = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "review.configure", values: { reviewModelId: model.id, semanticReviewEnabled: false, automaticReviewEnabled: false, level: "deep" } });
  assert.match(view.impact.message, /global Reviewer Specialist/); await approve(h.framework, view);
  assert.deepEqual(h.store.getSessionReviewerSpecialistSettings(h.session.id), { automaticReviewEnabled: false, level: "deep" });
  assert.equal(h.store.getSessionSettings(h.session.id).overrides.reviewModelId, model.id);
  assert.equal(h.store.getSessionSettings(h.session.id).overrides.semanticReviewEnabled, false);
  assert.deepEqual(h.store.getReviewerSpecialistSettings(), globalBefore);
  assert.deepEqual(h.store.getSessionReviewerSpecialistSettings(h.other.id), otherBefore);
});
test("CFG-T33: formal reviewer edits invalidate a previously generated configuration preview", async context => {
  const h = await harness(context);
  const view = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "review.configure", values: { level: "deep" } });
  const preview = await h.framework.preview(h.session.id, view.id, { expectedRevision: view.revision, values: { level: "deep" }, selectedOptionId: "persist" });
  await h.store.updateSessionReviewerSpecialistSettings(h.session.id, { automaticReviewEnabled: false, level: "quick" });
  await assert.rejects(h.framework.decide(h.session.id, view.id, { revision: preview.revision, previewDigest: preview.previewDigest!, selectedOptionId: "persist", decision: "apply" }), { code: "RESOURCE_VERSION_CONFLICT" });
  assert.equal(h.store.getSessionReviewerSpecialistSettings(h.session.id).level, "quick");
});
test("CFG-T29/34/35: inspection exposes sources and unsupported domains, never model tokens or connection URLs; unknown failures have no configuration remedy", async context => {
  const h = await harness(context);
  await h.store.createModel({ name: "Private API", model: "plain", baseUrl: "https://sensitive-endpoint.example/v1", apiToken: "credential-must-never-appear" });
  const tools = createConfigurationAgentTools({ store: h.store, sessionId: h.session.id, runId: "test-run" });
  const result = await tools[0]!.execute("inspect", {}); const raw = JSON.stringify(result);
  assert.ok(!raw.includes("credential-must-never-appear")); assert.ok(!raw.includes("sensitive-endpoint.example"));
  const output = JSON.parse(result.content[0]!.text);
  assert.equal(output.current.skillSelectionMode, "all"); assert.ok(output.sources);
  assert.ok(output.settingsInventory.some((row: { id: string; capabilities: string[] }) => row.id === "quotas" && row.capabilities.length === 0));
  assert.equal(output.capabilities.find((item: { id: string }) => item.id === "thinking.configure").available, false);
  for (const code of ["NETWORK_ERROR", "TIMEOUT", "RATE_LIMITED", "NO_RESULTS", "UNKNOWN"]) assert.deepEqual(configurationDiagnostic(code).capabilityIds, []);
  assert.deepEqual(configurationDiagnostic("CONNECTOR_DISABLED").capabilityIds, ["connector.enable"]);
  assert.deepEqual(tools.map(tool => tool.name), ["inspect_configuration", "propose_configuration"]);
});

test("CFG-R13: inspection separates registered, ready, actually mounted, saved and policy-blocked sources", async context => {
  const h = await harness(context);
  await h.store.replaceSessionSettings(h.session.id, { enabledConnectorIds: ["pubmed", "arxiv"], plugins: { "connector.chembl": { enabled: false } } });
  h.framework.options.connectors = () => [
    { id: "pubmed", name: "PubMed", ready: false, readiness: "unavailable", readinessReason: "MCP_CONNECTION_TIMEOUT", sameRunSupported: true },
    { id: "uniprot", name: "UniProt", ready: true, readiness: "ready", sameRunSupported: true },
    { id: "arxiv", name: "arXiv", ready: true, readiness: "ready", sameRunSupported: true },
    { id: "chembl", name: "ChEMBL", ready: true, readiness: "ready", sameRunSupported: true },
  ];
  const run = await h.store.createSessionRun({ sessionId: h.session.id, prompt: "Use a registered source", settingsSnapshot: h.store.resolveRuntimeSettings(h.session.id).effective });
  recordNativeRunChoiceContext({ store: h.store, sessionId: h.session.id, runId: run.id,
    enabledConnectorIds: ["arxiv"], plugins: run.settingsSnapshot.plugins, webSearchAvailable: false, toolCatalogFingerprint: "actual-mounted-arxiv" });
  const inspect = createConfigurationAgentTools({ store: h.store, sessionId: h.session.id, runId: run.id })[0]!;
  const result = await inspect.execute("inspect", {});
  const output = JSON.parse(result.content[0]!.text);
  const byId = new Map<string, Record<string, unknown>>(output.installedConnectors.map((row: Record<string, unknown>) => [row.id, row]));
  assert.deepEqual(output.current.enabledConnectorIds, ["arxiv"]);
  assert.ok(output.current.snapshotEnabledConnectorIds.includes("pubmed"));
  assert.equal(byId.get("pubmed")?.installed, true); assert.equal(byId.get("pubmed")?.ready, false);
  assert.equal(byId.get("pubmed")?.runtimeEnabled, false); assert.equal(byId.get("pubmed")?.sessionEnabled, true);
  assert.equal(byId.get("pubmed")?.readinessReason, "MCP_CONNECTION_TIMEOUT"); assert.equal(byId.get("pubmed")?.canPropose, false);
  assert.equal(byId.get("arxiv")?.runtimeEnabled, true); assert.equal(byId.get("arxiv")?.canPropose, false);
  assert.equal(byId.get("uniprot")?.canPropose, true); assert.equal(byId.get("uniprot")?.activationMode, "same_run");
  assert.equal(byId.get("chembl")?.ready, true); assert.equal(byId.get("chembl")?.policyAllowed, false);
  assert.equal(byId.get("chembl")?.unavailableReason, "CONNECTOR_POLICY_BLOCKED");
  const registered = h.framework.options.connectors(h.session.id);
  h.framework.options.connectors = () => registered.filter(row => row.id !== "uniprot");
  const unavailable = JSON.parse((await inspect.execute("inspect-again", {})).content[0]!.text);
  assert.equal(unavailable.capabilities.find((item: { id: string }) => item.id === "connector.enable").available, false);
  assert.equal(unavailable.capabilities.find((item: { id: string }) => item.id === "connector.enable").unsupportedReason, "NO_READY_CONFIGURABLE_CONNECTOR");
  assert.deepEqual(unavailable.sameRunCapabilities, []);
});
test("CFG-T29/33: Agent proposals use protocol v3 and skip redundant all-mode Skill enablement", async context => {
  const h = await harness(context); const tools = createConfigurationAgentTools({ store: h.store, sessionId: h.session.id, runId: "test-run" });
  const skipped = await tools[1]!.execute("all", { capabilityId: "skill.select", values: { enabledSkillIds: ["literature"] } });
  assert.equal(JSON.parse(skipped.content[0]!.text).status, "already_effective");
  assert.equal(h.framework.service.list(h.session.id).length, 0);
  const proposed = await tools[1]!.execute("select", { capabilityId: "skill.select", values: { skillSelectionMode: "selected", enabledSkillIds: ["literature"] }, reason: "Use only literature methods for subsequent tasks" });
  const { requestId } = JSON.parse(proposed.content[0]!.text); const view = h.framework.service.get(h.session.id, requestId);
  assert.equal(view.protocolVersion, 3); assert.equal(view.activation.mode, "next_run");
  assert.deepEqual(view.options?.map(option => option.kind), ["persisted_apply", "dismiss"]);
});
test("CFG-T33: unverified backend cannot propose or apply P1 changes", async context => {
  const h = await harness(context); const view = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "review.configure", values: { level: "deep" } });
  h.framework.options.nativeExecutor = false;
  await assert.rejects(h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "review.configure", values: { level: "quick" } }), { code: "CONFIGURATION_BACKEND_UNSUPPORTED" });
  await assert.rejects(approve(h.framework, view), { code: "CONFIGURATION_BACKEND_UNSUPPORTED" });
  assert.equal(h.store.getSessionReviewerSpecialistSettings(h.session.id).level, "quick");
});
test("CFG-T33: queue-time reviewer snapshot governs actual automatic review admission and level after Session preferences change", async context => {
  const h = await harness(context);
  await h.store.updateReviewerSpecialistSettings({ enabled: true });
  const oldRun = await h.store.createSessionRun({ sessionId: h.session.id, prompt: "Already queued report", settingsSnapshot: h.store.resolveRuntimeSettings(h.session.id).effective });
  assert.deepEqual(oldRun.settingsSnapshot.sessionReviewer, { automaticReviewEnabled: true, level: "quick" });
  const view = await h.framework.propose(h.session.id, { protocolVersion: 3, capabilityId: "review.configure", values: { automaticReviewEnabled: false, level: "deep" } });
  await approve(h.framework, view);
  const newRun = await h.store.createSessionRun({ sessionId: h.session.id, prompt: "New report", settingsSnapshot: h.store.resolveRuntimeSettings(h.session.id).effective });
  assert.deepEqual(newRun.settingsSnapshot.sessionReviewer, { automaticReviewEnabled: false, level: "deep" });
  const oldReport = await h.store.createArtifactVersion({ content: { hash: "a".repeat(64), size: 3 }, kind: "markdown", logicalName: "old-result.md", mediaType: "text/markdown", origin: "llm_declared", sessionId: h.session.id, sourcePath: "old-result.md", turnId: oldRun.id });
  const newReport = await h.store.createArtifactVersion({ content: { hash: "b".repeat(64), size: 3 }, kind: "markdown", logicalName: "new-result.md", mediaType: "text/markdown", origin: "llm_declared", sessionId: h.session.id, sourcePath: "new-result.md", turnId: newRun.id });
  const executions: Array<{ sourceRunId?: string; reviewLevel: string }> = [];
  const coordinator = new ReviewerAuditCoordinator(h.store, { run: async task => { executions.push(task); return []; } }, { quickBatchQuietMs: 0 });
  h.cleanup.push(() => coordinator.cancelSession(h.session.id));
  const pending = await coordinator.enqueueArtifactVersion({ artifactVersionId: oldReport.version.id, contentHash: oldReport.version.content.hash, mediaType: oldReport.version.mediaType, sessionId: h.session.id });
  assert.equal(pending?.sourceRunId, oldRun.id); assert.equal(pending?.reviewLevel, "quick");
  const ignored = await coordinator.enqueueArtifactVersion({ artifactVersionId: newReport.version.id, contentHash: newReport.version.content.hash, mediaType: newReport.version.mediaType, sessionId: h.session.id });
  assert.equal(ignored, undefined);
  const deadline = Date.now() + 5000;
  while (!(await h.store.listReviewerAuditTasks(h.session.id)).some(task => task.id === pending!.id && task.status === "completed")) {
    if (Date.now() >= deadline) assert.fail("Frozen automatic review did not complete");
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.equal(executions.length, 1); assert.equal(executions[0]!.sourceRunId, oldRun.id); assert.equal(executions[0]!.reviewLevel, "quick");
  assert.deepEqual((await h.store.getSessionRun(h.session.id, oldRun.id))?.settingsSnapshot, oldRun.settingsSnapshot);
});
