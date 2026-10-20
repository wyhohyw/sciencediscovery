// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { createTest } from "../../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:st", "os:linux", "arch:amd64", "arch:arm64", "model:mock", "isolation:fresh"] });
import type { TestContext } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { resolve, sep } from "node:path";
import type { McpTransportClient } from "@sciencediscovery/data-source";
import { createPublicBiomedSources } from "@sciencediscovery/mcp-sources";
import type { ConfigurationRequestView, McpCatalog, McpInvocation, McpInvokeResponse, ModelProfile, Project, Session, SessionDetail, SessionRun, SessionRunEvent } from "@sciencediscovery/schema";
import { createApiServer, closeApiServer } from "../http/index.js";
import type { ServerConfig } from "../bootstrap/config.js";

type ModelMessage = { role?: string; content?: unknown; tool_call_id?: string; tool_calls?: Array<{ id?: string; function?: { name?: string } }> };
type ModelRequest = { stream?: boolean; tools?: Array<{ function?: { name?: string } }>; messages?: ModelMessage[] };
async function listen(server: Server) {
  await new Promise<void>((done, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", done); });
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}
async function close(server: Server) { await new Promise<void>(done => { server.close(() => done()); server.closeAllConnections(); }); }
async function body(request: IncomingMessage): Promise<ModelRequest> {
  const parts: Buffer[] = []; for await (const part of request) parts.push(Buffer.from(part));
  return parts.length ? JSON.parse(Buffer.concat(parts).toString("utf8")) as ModelRequest : {};
}
async function fixture(context: TestContext, decision: "apply" | "dismiss", automatic = false) {
  const tmp = resolve(process.cwd(), ".tmp"); await mkdir(tmp, { recursive: true });
  const root = await mkdtemp(resolve(tmp, "configuration-agent-"));
  const oldExecutor = process.env.SCIENCE_AGENT_EXECUTOR; process.env.SCIENCE_AGENT_EXECUTOR = "native";
  const modelRequests: ModelRequest[] = []; const issued: string[] = []; let invocations = 0;
  const provider = createServer(async (request, response) => {
    const input = await body(request); const task = Boolean(input.tools?.length);
    if (task) modelRequests.push(input);
    const results = new Set((input.messages ?? []).filter(item => item.role === "tool").map(item => item.tool_call_id));
    const queued = (input.messages ?? []).some(item => item.role === "user" && String(item.content).includes("QUEUE_MARKER"));
    let name: string | undefined, id: string | undefined, args: unknown, text = "Fixture title";
    if (task && queued) text = "QUEUED_TASK_DONE";
    else if (task && !results.has("cfg-inspect")) { name = "inspect_configuration"; id = "cfg-inspect"; args = {}; }
    else if (task && !results.has("cfg-propose")) { name = "propose_configuration"; id = "cfg-propose"; args = { capabilityId: "connector.enable", values: { connectorId: "pubmed" } }; }
    else if (task && decision === "dismiss") text = "CONFIGURATION_DECLINED_CONTINUED";
    else if (task && !results.has("cfg-discover")) { name = "tool_search"; id = "cfg-discover"; args = { query: "select:mcp__pubmed__search" }; }
    else if (task && !results.has("cfg-pubmed")) { name = "mcp__pubmed__search"; id = "cfg-pubmed"; args = { query: "diabetes systematic review", limit: 1 }; }
    else if (task) text = "CONFIGURATION_TASK_COMPLETE: the offline PubMed search completed.";
    if (name) issued.push(name);
    if (!input.stream) {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ id: "fixture", choices: [{ message: { role: "assistant", content: text }, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1 } })); return;
    }
    response.writeHead(200, { "content-type": "text/event-stream" });
    const delta = name ? { role: "assistant", tool_calls: [{ index: 0, id, type: "function", function: { name, arguments: JSON.stringify(args) } }] } : { role: "assistant", content: text };
    const chunk = (value: unknown, finish: string | null) => ({ id: `fixture-${id ?? "final"}`, object: "chat.completion.chunk", created: 1, model: "configuration-agent-fixture", choices: [{ index: 0, delta: value, finish_reason: finish }] });
    response.write(`data: ${JSON.stringify(chunk(delta, null))}\n\n`);
    response.write(`data: ${JSON.stringify(chunk({}, name ? "tool_calls" : "stop"))}\n\n`);
    response.end("data: [DONE]\n\n");
  });
  const providerOrigin = await listen(provider);
  const runner = createServer((request, response) => { request.resume(); response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(request.url === "/health" ? { npuBroker: { enabled: false }, scientificEnvs: { available: false, enabled: false }, sandbox: { available: false } } : {})); });
  const runnerOrigin = await listen(runner);
  const manifest = createPublicBiomedSources(["pubmed"])[0]!.manifest;
  const catalog = { loadedAt: "2026-10-19T00:00:00.000Z", revision: "configuration-agent-offline-pubmed",
    servers: [{ enabled: true, id: manifest.transport.mcpServerId, transport: "stdio", tools: Object.values(manifest.tools)
      .map(tool => ({ name: tool.mcpToolName ?? tool.id, description: tool.description, schemaHash: `fixture-${tool.id}`, inputSchema: tool.inputSchema })) }] } as unknown as McpCatalog;
  const mcpTransport: McpTransportClient = { catalog: async () => catalog, reload: async () => catalog,
    invoke: async request => {
      assert.equal(request.toolName, "pubmed_search"); assert.equal(request.context.toolCallId, "cfg-pubmed");
      invocations++;
      return { requestId: request.requestId, serverId: request.serverId, toolName: request.toolName, durationMs: 1, isError: false, attempts: [],
        content: [{ type: "json", value: { attribution: "Offline fixture", license: "Fixture data", records: [], retrievedAt: "2026-10-19T00:00:00.000Z", sourceId: "pubmed", toolId: "search", untrusted: true, warnings: [] } }] } as unknown as McpInvokeResponse;
    } };
  const config: ServerConfig = { authToken: "configuration-agent-token", dataDir: root, host: "127.0.0.1", port: 0,
    modelCatalogPath: resolve(root, "absent-models.json"), paperPythonPath: resolve(root, "no-python"), paperWorkerPath: resolve(root, "no-worker"),
    gatewayIdleTimeoutMs: 15_000, gatewayTurnTimeoutMs: 0, kernelIdleTimeoutMs: 0, permissionWaitTimeoutMs: 0,
    runnerExecTimeoutMs: 0, runnerMaxOutputBytes: 1_000_000, runnerMaxWorkspaceBytes: 10_737_418_240, runnerToken: "runner-token", runnerUrl: runnerOrigin,
    sshConfigPath: resolve(root, "ssh-config"), staticDir: resolve(root, "no-web"), workspaceUpload: { maxFileBytes: 1_000_000, maxRequestBytes: 10_000_000, maxWorkspaceBytes: 10_737_418_240 },
    memoryGraph: { url: "", internalToken: "" }, evolve: { url: "", internalToken: "" } };
  let api: Server | undefined;
  context.after(async () => {
    try { if (api) await closeApiServer(api); } finally {
      await close(provider); await close(runner);
      if (oldExecutor === undefined) delete process.env.SCIENCE_AGENT_EXECUTOR; else process.env.SCIENCE_AGENT_EXECUTOR = oldExecutor;
      assert.ok(root.startsWith(tmp + sep)); await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    }
  });
  api = createApiServer(config, { mcpTransport }); const origin = await listen(api);
  async function call<T>(path: string, input?: unknown, method = input === undefined ? "GET" : "POST") {
    let response: Response;
    try { response = await fetch(origin + path, { method, headers: { authorization: "Bearer configuration-agent-token", "content-type": "application/json", "Idempotency-Key": randomUUID() },
      ...(input === undefined ? {} : { body: JSON.stringify(input) }), signal: AbortSignal.timeout(60_000) }); }
    catch { throw new Error(`Fixture HTTP timeout: ${method} ${path}; issued=${issued.join(",")}`); }
    const text = await response.text(); return { status: response.status, value: JSON.parse(text) as T, text };
  }
  async function ok<T>(path: string, input?: unknown, method?: string, status = input === undefined ? 200 : 201): Promise<T> {
    const result = await call<T>(path, input, method); assert.equal(result.status, status, `${path}: ${result.text}`); return result.value;
  }
  const model = await ok<ModelProfile>("/api/models", { name: "Configuration agent fixture", model: "configuration-agent-fixture", baseUrl: `${providerOrigin}/v1`, apiToken: "fixture-token" });
  await ok("/api/settings", { modelId: model.id, enabledConnectorIds: [], semanticReviewEnabled: false }, "PUT", 200);
  const project = await ok<Project>("/api/projects", { name: "Configuration agent runtime", settingsOverrides: { enabledSkillIds: [], skillSelectionMode: "selected" } });
  const session = await ok<Session>(`/api/projects/${project.id}/sessions`, { title: "Configuration runtime fixture", approvalMode: "always_allow", autoApplyLowRiskConfiguration: automatic });
  const path = `/api/sessions/${session.id}`;
  async function waitRun(id: string, status: SessionRun["status"]): Promise<SessionRun> {
    let last: SessionRun | undefined;
    for (let attempt = 0; attempt < 300; attempt++) {
      last = (await ok<SessionRun[]>(`${path}/runs`)).find(item => item.id === id);
      if (last?.status === status) return last;
      if (last && ["failed", "cancelled", "completed"].includes(last.status)) throw new Error(`Run ended as ${last.status} instead of ${status}: ${last.error ?? ""}; issued=${issued.join(",")}`);
      await new Promise(done => setTimeout(done, 50));
    }
    throw new Error(`Timed out waiting for ${status}; latest=${JSON.stringify(last)}; issued=${issued.join(",")}`);
  }
  async function start() {
    // Avoid the separate explicit-connector preflight: the actual model must discover and propose it.
    const prompt = "Find a literature result using an installed source that is currently disabled. Choose a source yourself, configure it if needed, and finish the original lookup without delegating.";
    const run = await ok<SessionRun>(`${path}/runs`, { content: prompt });
    const blocked = await waitRun(run.id, "blocked"); assert.equal(blocked.configurationWait, true); assert.ok(blocked.configurationCheckpoint?.historyRef);
    const cards = await ok<{ items: ConfigurationRequestView[] }>(`${path}/configuration-requests`);
    const card = cards.items.find(item => item.runId === run.id && item.capabilityId === "connector.enable");
    assert.ok(card, "The model's generic proposal must expose a V2 card after checkpoint commit");
    assert.equal(card.state, "awaiting_confirmation"); assert.ok(card.previewDigest); assert.ok(card.revision);
    if (!automatic) assert.equal(card.autoApplyAt, undefined, "Tool Always Allow does not authorize configuration changes");
    const events = await ok<SessionRunEvent[]>(`${path}/runs/${run.id}/events`);
    assert.equal(events.filter(item => item.event.type === "run.completed").length, 0);
    assert.equal(blocked.finishedAt, undefined);
    return { run: blocked, card, prompt };
  }
  const decide = (card: ConfigurationRequestView) => call<ConfigurationRequestView>(`${path}/configuration-requests/${card.id}/decision`, { revision: card.revision, previewDigest: card.previewDigest, decision });
  return { root, path, session, origin, modelRequests, issued, ok, call, start, decide, waitRun, invocations: () => invocations };
}

test("CFG Agent runtime: generic proposal blocks, approval resumes the same run, real MCP search executes once, queue follows", async context => {
  const f = await fixture(context, "apply"); const original = await f.start();
  const later = await f.ok<SessionRun>(`${f.path}/runs`, { content: "QUEUE_MARKER: respond after the original task finishes." });
  await new Promise(done => setTimeout(done, 150));
  const held = (await f.ok<SessionRun[]>(`${f.path}/runs`)).find(item => item.id === later.id)!;
  assert.equal(held.status, "queued"); assert.equal(held.startedAt, undefined);
  const decision = await f.decide(original.card); assert.equal(decision.status, 200, decision.text);
  const completed = await f.waitRun(original.run.id, "completed"); const laterCompleted = await f.waitRun(later.id, "completed");
  assert.ok(laterCompleted.startedAt! >= completed.finishedAt!, "The queued run must start only after the original run ends");
  assert.equal(completed.id, original.run.id); assert.equal(completed.userMessageId, original.run.userMessageId);
  assert.equal(f.invocations(), 1);
  for (const name of ["inspect_configuration", "propose_configuration", "mcp__pubmed__search"]) assert.equal(f.issued.filter(item => item === name).length, 1, `${name} must not replay`);
  const events = await f.ok<SessionRunEvent[]>(`${f.path}/runs/${completed.id}/events`);
  assert.equal(events.filter(item => item.event.type === "run.completed").length, 1);
  const searches = await f.ok<McpInvocation[]>(`${f.path}/mcp/invocations`); assert.equal(searches.filter(item => item.sourceId === "pubmed").length, 1);
  const detail = await f.ok<SessionDetail>(f.path);
  assert.equal(detail.messages.filter(message => message.role === "user" && message.content === original.prompt).length, 1);
  assert.equal(detail.messages.filter(message => message.role === "assistant" && message.content.includes("CONFIGURATION_TASK_COMPLETE")).length, 1);
  const saved = await f.ok<ConfigurationRequestView>(`${f.path}/configuration-requests/${original.card.id}`); assert.equal(saved.activation.state, "active");
  assert.ok(f.modelRequests.some(request => JSON.stringify(request.messages).includes("The user approved enabling")), "The actual resumed model receives the committed decision");
});

test("CFG Agent runtime: refusing the V2 card continues original history without enabling or replaying tools", async context => {
  const f = await fixture(context, "dismiss"); const original = await f.start();
  assert.equal((await f.decide(original.card)).status, 200);
  const completed = await f.waitRun(original.run.id, "completed"); assert.equal(completed.userMessageId, original.run.userMessageId);
  assert.equal(f.invocations(), 0); assert.equal(f.issued.filter(name => name === "propose_configuration").length, 1);
  const detail = await f.ok<SessionDetail>(f.path);
  assert.equal(detail.messages.filter(message => message.role === "assistant" && message.content.includes("CONFIGURATION_DECLINED_CONTINUED")).length, 1);
  assert.ok(f.modelRequests.some(request => JSON.stringify(request.messages).includes("The user declined enabling")));
});

test("CFG Agent runtime: Stop invalidates a pending V2 card and never resumes the stopped source run", async context => {
  const f = await fixture(context, "apply"); const original = await f.start();
  const stop = await f.call(`${f.path}/run/cancel`, {}, "POST"); assert.ok([200, 202].includes(stop.status), stop.text);
  await f.waitRun(original.run.id, "cancelled");
  const lateDecision = await f.decide(original.card); assert.ok([409, 410].includes(lateDecision.status), lateDecision.text);
  await new Promise(done => setTimeout(done, 200));
  const runs = await f.ok<SessionRun[]>(`${f.path}/runs`);
  assert.equal(runs.find(item => item.id === original.run.id)!.status, "cancelled"); assert.equal(runs.length, 1);
  assert.equal(f.invocations(), 0); assert.equal(f.issued.length, 2);
});

test("CFG Agent runtime: separate low-risk authorization waits for the real server deadline and auto-resumes the same run", async context => {
  const f = await fixture(context, "apply", true); const original = await f.start();
  assert.ok(original.card.autoApplyAt, "The generic card must show its server-owned automatic deadline");
  const deadline = Date.parse(original.card.autoApplyAt);
  const created = Date.parse(original.card.createdAt);
  assert.ok(deadline - created >= 29_000 && deadline - created <= 31_000, "The configured grace period is approximately thirty seconds");
  assert.equal(f.invocations(), 0, "No dependent MCP action can run before the configuration deadline");
  const during = (await f.ok<SessionRun[]>(`${f.path}/runs`)).find(item => item.id === original.run.id)!;
  assert.equal(during.status, "blocked"); assert.equal(during.configurationWait, true);
  // No decision/continue/browser request: the real API timer owns the entire transition.
  let completed: SessionRun | undefined;
  for (let attempt = 0; attempt < 160; attempt++) {
    const current = (await f.ok<SessionRun[]>(`${f.path}/runs`)).find(item => item.id === original.run.id)!;
    if (current.status === "completed") { completed = current; break; }
    assert.ok(!["failed", "cancelled", "interrupted"].includes(current.status), `Automatic continuation failed: ${JSON.stringify(current)}`);
    if (Date.now() < deadline - 1_000) assert.equal(f.invocations(), 0, "Grace period must not invoke the connector early");
    await new Promise(done => setTimeout(done, 500));
  }
  assert.ok(completed, "The real server timer must resume and complete without a user decision");
  assert.equal(completed.id, original.run.id); assert.equal(completed.userMessageId, original.run.userMessageId);
  assert.equal(f.invocations(), 1); assert.equal(f.issued.filter(name => name === "propose_configuration").length, 1);
  const saved = await f.ok<ConfigurationRequestView>(`${f.path}/configuration-requests/${original.card.id}`);
  assert.equal(saved.state, "applied"); assert.equal(saved.decisionSource, "auto"); assert.equal(saved.activation.state, "active");
  const events = await f.ok<SessionRunEvent[]>(`${f.path}/runs/${original.run.id}/events`);
  assert.equal(events.filter(item => item.event.type === "run.completed").length, 1);
  const detail = await f.ok<SessionDetail>(f.path);
  assert.equal(detail.messages.filter(message => message.role === "assistant" && message.content.includes("CONFIGURATION_TASK_COMPLETE")).length, 1);
});
