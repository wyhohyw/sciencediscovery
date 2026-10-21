// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import assert from "node:assert/strict";
import type { TestContext } from "node:test";
import { createServer, type Server, type IncomingMessage } from "node:http";
import type { AddressInfo } from "node:net";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { randomUUID } from "node:crypto";
import { createTest } from "../../../../test/support/tagged/compat.mjs";
import type { ConfigurationRequestView, CommitSkillLibraryVersionResult, ModelProfile, Project, Session, SessionRun, SkillLibrary, ScientificArtifact, ScientificArtifactVersion, ReviewCheckpointResult } from "@sciencediscovery/schema";
import { createApiServer, closeApiServer } from "../http/index.js";
import type { ServerConfig } from "../bootstrap/config.js";
import { offlineMcpTransport } from "../mcp/offline-transport.fixture.js";
const { test } = createTest(import.meta.url, { tags: ["category:st", "os:linux", "arch:amd64", "arch:arm64", "model:mock", "isolation:fresh"] });

type WireMessage = { role?: string; content?: unknown; tool_call_id?: string };
type WireRequest = { stream?: boolean; messages?: WireMessage[]; tools?: Array<{ function?: { name?: string } }>; thinking?: unknown; reasoning_effort?: unknown };
async function listen(server: Server): Promise<string> {
  await new Promise<void>((done, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", done); });
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}
async function close(server: Server) { await new Promise<void>(done => { server.close(() => done()); server.closeAllConnections(); }); }
async function readRequest(request: IncomingMessage): Promise<WireRequest> {
  const chunks: Buffer[] = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) as WireRequest : {};
}
const nextTaskPrompt = "Use the configured workflow to review literature and load both selected research methods. Work without delegation.";
const librarySkillId = "p1-runtime-method";
const witness = "P1_LIBRARY_RUNTIME_WITNESS_739";

async function fixture(context: TestContext, mode: "settings" | "review" = "settings") {
  const tmp = resolve(process.cwd(), ".tmp"); await mkdir(tmp, { recursive: true });
  const root = await mkdtemp(resolve(tmp, "configuration-p1-runtime-"));
  const oldExecutor = process.env.SCIENCE_AGENT_EXECUTOR; process.env.SCIENCE_AGENT_EXECUTOR = "native";
  const requests: WireRequest[] = [];
  let releaseBlocker!: () => void;
  const blocker = new Promise<void>(done => { releaseBlocker = done; });
  let reportVersionId: string | undefined;
  const provider = createServer(async (request, response) => {
    const input = await readRequest(request);
    const task = Boolean(input.tools?.length);
    if (task) requests.push(input);
    const messages = input.messages ?? [];
    const completed = new Set(messages.filter(message => message.role === "tool").map(message => message.tool_call_id));
    const nextTask = messages.some(message => message.role === "user" && String(message.content).includes(nextTaskPrompt));
    let tool: string | undefined, id: string | undefined, args: unknown;
    let text = "Fixture title";
    const latestUser = messages.filter(message => message.role === "user").at(-1);
    if (mode === "review" && task) {
      // Runtime review observations arrive as a later user-role data message.
      // Select the actual task instruction, not that observation or a quoted task.
      const taskInstruction = messages.filter(message => message.role === "user" && String(message.content).trim().startsWith("P1_REVIEW_")).at(-1);
      const prompt = String(taskInstruction?.content ?? latestUser?.content);
      const marker = ["P1_REVIEW_BLOCKER", "P1_REVIEW_OLD", "P1_REVIEW_NEW", "P1_REVIEW_GATE_DISABLED"]
        .map(value => ({ value, index: prompt.lastIndexOf(value) })).sort((left, right) => right.index - left.index)[0];
      if (marker?.index !== -1 && marker?.value === "P1_REVIEW_BLOCKER") { await blocker; text = "BLOCKER_RELEASED"; }
      else if (marker?.index !== -1 && (marker?.value === "P1_REVIEW_OLD" || marker?.value === "P1_REVIEW_NEW")) {
        const checkpointId = marker.value === "P1_REVIEW_OLD" ? "p1-checkpoint-old" : "p1-checkpoint-new";
        if (!completed.has(checkpointId)) { tool = "review_checkpoint"; id = checkpointId; args = { artifactVersionIds: [reportVersionId], reason: "Review this fixed report with the task's approved review policy." }; }
        else text = "FROZEN_REVIEW_COMPLETE";
      } else text = "GLOBAL_REVIEW_DISABLED";
    } else if (task && nextTask) {
      if (!completed.has("p1-read-method")) { tool = "read_skill"; id = "p1-read-method"; args = { skillId: librarySkillId }; }
      else if (!completed.has("p1-read-literature")) { tool = "read_skill"; id = "p1-read-literature"; args = { skillId: "literature-searcher" }; }
      else text = "CONFIGURED_WORKFLOW_COMPLETE";
    } else if (task && !completed.has("p1-inspect")) { tool = "inspect_configuration"; id = "p1-inspect"; args = {}; }
    else if (task && !completed.has("p1-skills")) { tool = "propose_configuration"; id = "p1-skills"; args = {
      capabilityId: "skill.select", values: { skillSelectionMode: "selected", enabledSkillIds: ["literature-searcher"], enabledSkillLibraryIds: ["p1-runtime-library"] }, reason: "Use only the requested installed literature workflow and methods library for subsequent research." }; }
    else if (task && !completed.has("p1-thinking")) { tool = "propose_configuration"; id = "p1-thinking"; args = {
      capabilityId: "thinking.configure", values: { thinkingMode: "enabled", thinkingEffort: "max" }, reason: "Enable the requested supported deep thinking mode for subsequent tasks." }; }
    else if (task) text = "SETTINGS_PROPOSED_FOR_FUTURE";
    if (!input.stream) {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: text }, finish_reason: "stop" }] })); return;
    }
    const delta = tool ? { role: "assistant", tool_calls: [{ index: 0, id, type: "function", function: { name: tool, arguments: JSON.stringify(args) } }] } : { role: "assistant", content: text };
    const chunk = (value: unknown, finish: string | null) => ({ id: `fixture-${id ?? "final"}`, object: "chat.completion.chunk", created: 1, model: "p1-fixture", choices: [{ index: 0, delta: value, finish_reason: finish }] });
    response.writeHead(200, { "content-type": "text/event-stream" });
    response.write(`data: ${JSON.stringify(chunk(delta, null))}\n\n`);
    response.write(`data: ${JSON.stringify(chunk({}, tool ? "tool_calls" : "stop"))}\n\n`);
    response.end("data: [DONE]\n\n");
  });
  const providerOrigin = await listen(provider);
  const runner = createServer((request, response) => { request.resume(); response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(request.url === "/health" ? { npuBroker: { enabled: false }, scientificEnvs: { available: false, enabled: false }, sandbox: { available: false } } : {})); });
  const runnerOrigin = await listen(runner);
  const config: ServerConfig = { authToken: "configuration-p1-runtime-token", dataDir: root, host: "127.0.0.1", port: 0,
    modelCatalogPath: resolve(root, "absent-models.json"), paperPythonPath: resolve(root, "no-python"), paperWorkerPath: resolve(root, "no-worker"),
    gatewayIdleTimeoutMs: 60_000, gatewayTurnTimeoutMs: 0, kernelIdleTimeoutMs: 0, permissionWaitTimeoutMs: 0,
    runnerExecTimeoutMs: 0, runnerMaxOutputBytes: 1_000_000, runnerMaxWorkspaceBytes: 10_737_418_240, runnerToken: "runner-token", runnerUrl: runnerOrigin,
    sshConfigPath: resolve(root, "ssh-config"), staticDir: resolve(root, "no-web"), workspaceUpload: { maxFileBytes: 1_000_000, maxRequestBytes: 10_000_000, maxWorkspaceBytes: 10_737_418_240 },
    memoryGraph: { url: "", internalToken: "" }, evolve: { url: "", internalToken: "" } };
  let api: Server | undefined;
  context.after(async () => {
    releaseBlocker();
    try { if (api) await closeApiServer(api); } finally {
      await close(provider); await close(runner);
      if (oldExecutor === undefined) delete process.env.SCIENCE_AGENT_EXECUTOR; else process.env.SCIENCE_AGENT_EXECUTOR = oldExecutor;
      assert.ok(root.startsWith(tmp + sep)); await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    }
  });
  api = createApiServer(config, { mcpTransport: offlineMcpTransport }); const origin = await listen(api);
  async function ok<T>(path: string, input?: unknown, method = input === undefined ? "GET" : "POST", expected = input === undefined ? 200 : 201): Promise<T> {
    const response = await fetch(origin + path, { method, headers: { authorization: "Bearer configuration-p1-runtime-token", "content-type": "application/json", "Idempotency-Key": randomUUID() },
      ...(input === undefined ? {} : { body: JSON.stringify(input) }), signal: AbortSignal.timeout(60_000) });
    const text = await response.text(); assert.equal(response.status, expected, `${method} ${path}: ${text}`); return JSON.parse(text) as T;
  }
  const model = await ok<ModelProfile>("/api/models", { name: "P1 native wire fixture", model: "p1-fixture", baseUrl: `${providerOrigin}/v1`, apiToken: "fixture-token",
    apiProtocol: "openai-chat-completions", apiVariant: "deepseek", thinkingMode: "disabled", thinkingEffort: "low",
    facts: { thinkingSupported: true, thinkingEfforts: ["low", "max"] } });
  assert.deepEqual(model.facts?.thinkingEfforts, ["low", "max"], "The fixture explicitly declares only the effort values accepted by its provider");
  await ok("/api/settings", { modelId: model.id, enabledConnectorIds: [], semanticReviewEnabled: false }, "PUT", 200);
  await ok<SkillLibrary>("/api/skill-libraries", { id: "p1-runtime-library", name: "P1 methods library" });
  const committed = await ok<CommitSkillLibraryVersionResult>("/api/skill-libraries/p1-runtime-library/versions", { author: { kind: "user" }, operations: [{ type: "upsert", package: { files: [{ path: "SKILL.md",
    content: `---\nname: ${librarySkillId}\ndescription: Research literature review methods for configured workflow evidence\n---\n\n# Research literature methods\n\n${witness}\nUse bounded literature review methods.\n` }] } }] });
  assert.ok(committed.version?.id);
  const project = await ok<Project>("/api/projects", { name: "P1 real runtime assembly", settingsOverrides: { enabledSkillIds: [], skillSelectionMode: "selected" } });
  const session = await ok<Session>(`/api/projects/${project.id}/sessions`, { title: "P1 future run", approvalMode: "always_allow" });
  const path = `/api/sessions/${session.id}`;
  if (mode === "review") {
    await ok("/api/reviewer-specialist/settings", { enabled: true }, "PUT", 200);
    await ok(`${path}/reviewer-specialist/settings`, { automaticReviewEnabled: false, level: "quick" }, "PUT", 200);
    await ok(`${path}/files`, { path: "reports/frozen-review.md", content: "# Review routing fixture\n\nThis report describes a bounded research workflow without numerical or literature claims.\n" });
    const artifact = (await ok<ScientificArtifact[]>(`${path}/artifacts`)).find(item => item.logicalName.endsWith("frozen-review.md"));
    assert.ok(artifact);
    reportVersionId = (await ok<ScientificArtifactVersion[]>(`${path}/artifacts/${artifact.id}/versions`))[0]?.id;
    assert.ok(reportVersionId);
  }
  async function waitRun(id: string): Promise<SessionRun> {
    let latest: SessionRun | undefined;
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      latest = (await ok<SessionRun[]>(`${path}/runs`)).find(run => run.id === id);
      if (latest?.status === "completed") return latest;
      if (latest && ["failed", "cancelled", "interrupted"].includes(latest.status)) throw new Error(`P1 run ${latest.status}: ${latest.error ?? ""}`);
      await new Promise(done => setTimeout(done, 50));
    }
    throw new Error(`P1 run timed out: ${latest?.status}`);
  }
  return { ok, path, requests, waitRun, releaseBlocker, libraryVersionId: committed.version.id };
}

test("CFG-T33 Native /runs: confirmed Skill/Library and thinking cards change actual next-run tool context and provider wire fields", async context => {
  const f = await fixture(context);
  const original = await f.ok<SessionRun>(`${f.path}/runs`, { content: "For future research, use only the installed literature-searcher workflow and the P1 methods library, and enable maximum supported deep thinking. Work in the main Agent." });
  await f.waitRun(original.id);
  const originalRequests = [...f.requests];
  assert.ok(originalRequests.length >= 4);
  assert.ok(originalRequests.every(request => request.reasoning_effort !== "max"));
  const originalSystem = originalRequests.flatMap(request => request.messages ?? []).filter(message => message.role === "system").map(message => String(message.content)).join("\n");
  assert.equal(originalSystem.includes("<name>literature-searcher</name>"), false, "The original task cannot acquire a proposed future Skill before approval");
  assert.equal(originalSystem.includes(`<name>${librarySkillId}</name>`), false);
  const cards = await f.ok<{ items: ConfigurationRequestView[] }>(`${f.path}/configuration-requests`);
  assert.deepEqual(cards.items.map(card => card.capabilityId).sort(), ["skill.select", "thinking.configure"]);
  for (const card of cards.items) {
    assert.equal(card.protocolVersion, 3); assert.equal(card.activation.mode, "next_run");
    const values = Object.fromEntries(card.fields.flatMap(field => field.kind !== "credential" && field.value !== undefined ? [[field.id, field.value]] : []));
    const prepared = await f.ok<ConfigurationRequestView>(`${f.path}/configuration-requests/${card.id}/preview`, { expectedRevision: card.revision, values, selectedOptionId: card.selectedOptionId }, "POST", 200);
    const applied = await f.ok<ConfigurationRequestView>(`${f.path}/configuration-requests/${card.id}/decision`, { revision: prepared.revision, previewDigest: prepared.previewDigest, selectedOptionId: prepared.selectedOptionId, decision: "apply" }, "POST", 200);
    assert.equal(applied.state, "applied");
  }
  const boundary = f.requests.length;
  const next = await f.ok<SessionRun>(`${f.path}/runs`, { content: nextTaskPrompt });
  const finished = await f.waitRun(next.id);
  assert.equal(finished.settingsSnapshot.enabledSkillLibraries[0]?.versionId, f.libraryVersionId);
  const actual = f.requests.slice(boundary);
  assert.ok(actual.length >= 3, "Both real read_skill calls must reach the native runtime");
  assert.ok(actual.every(request => JSON.stringify(request.thinking) === JSON.stringify({ type: "enabled" }) && request.reasoning_effort === "max"), "Confirmed thinking fields must be sent to the real provider client");
  const system = actual.flatMap(request => request.messages ?? []).filter(message => message.role === "system").map(message => String(message.content)).join("\n");
  assert.ok(system.includes("<name>literature-searcher</name>")); assert.ok(system.includes(`<name>${librarySkillId}</name>`), "The selected library skill must be recalled into actual runtime skill metadata");
  const methodRead = actual.flatMap(request => request.messages ?? []).find(message => message.role === "tool" && message.tool_call_id === "p1-read-method");
  assert.ok(String(methodRead?.content).includes(witness), "read_skill must return the frozen library instructions, not merely a saved settings ID");
  const installedRead = actual.flatMap(request => request.messages ?? []).find(message => message.role === "tool" && message.tool_call_id === "p1-read-literature");
  assert.ok(String(installedRead?.content).includes("# Literature Searcher Skill"), "The installed Skill must load its actual frozen instructions");
});

test("CFG-T33 Native /runs: queued quick and deep tasks consume their own frozen review policy while the global gate remains live", async context => {
  const f = await fixture(context, "review");
  const blocker = await f.ok<SessionRun>(`${f.path}/runs`, { content: "P1_REVIEW_BLOCKER: wait briefly before completing this independent task." });
  for (let attempt = 0; attempt < 300 && !f.requests.some(request => JSON.stringify(request.messages).includes("P1_REVIEW_BLOCKER")); attempt++)
    await new Promise(done => setTimeout(done, 50));
  assert.ok(f.requests.some(request => JSON.stringify(request.messages).includes("P1_REVIEW_BLOCKER")));
  const quick = await f.ok<SessionRun>(`${f.path}/runs`, { content: "P1_REVIEW_OLD: use Reviewer Specialist to review the fixed report using this task's saved review policy." });
  assert.equal(quick.settingsSnapshot.sessionReviewer?.level, "quick");
  await f.ok(`${f.path}/reviewer-specialist/settings`, { automaticReviewEnabled: false, level: "deep" }, "PUT", 200);
  const deep = await f.ok<SessionRun>(`${f.path}/runs`, { content: "P1_REVIEW_NEW: use Reviewer Specialist to review the fixed report using this task's saved review policy." });
  assert.equal(deep.settingsSnapshot.sessionReviewer?.level, "deep");
  f.releaseBlocker(); await f.waitRun(blocker.id); await f.waitRun(quick.id); await f.waitRun(deep.id);
  const result = (id: string) => {
    const message = f.requests.flatMap(request => request.messages ?? []).find(item => item.role === "tool" && item.tool_call_id === id);
    assert.ok(message, `The actual Native review_checkpoint result ${id} must return to the model; captured tool results: ${f.requests.flatMap(request => request.messages ?? []).filter(item => item.role === "tool").map(item => item.tool_call_id).join(",")}`);
    return JSON.parse(String(message.content)) as ReviewCheckpointResult;
  };
  const oldResult = result("p1-checkpoint-old"), newResult = result("p1-checkpoint-new");
  assert.equal(oldResult.checkpoint.parentRunId, quick.id); assert.equal(newResult.checkpoint.parentRunId, deep.id);
  assert.equal(oldResult.reviews[0]?.reviewLevel, "quick", "An already queued task must not adopt a later session Deep setting");
  assert.equal(newResult.reviews[0]?.reviewLevel, "deep", "The newly queued task must actually invoke Deep review routing");
  await f.ok("/api/reviewer-specialist/settings", { enabled: false }, "PUT", 200);
  const boundary = f.requests.length;
  const disabled = await f.ok<SessionRun>(`${f.path}/runs`, { content: "P1_REVIEW_GATE_DISABLED: use Reviewer Specialist if it is available, otherwise explain its current availability." });
  await f.waitRun(disabled.id);
  assert.equal(disabled.settingsSnapshot.sessionReviewer?.level, "deep");
  assert.ok(f.requests.slice(boundary).length);
  assert.ok(f.requests.slice(boundary).every(request => !request.tools?.some(tool => tool.function?.name === "review_checkpoint")), "Frozen Deep preferences cannot bypass the live global Reviewer Specialist gate");
});
