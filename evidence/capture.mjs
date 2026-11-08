// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
/** E2E-META
 * Purpose: replace/clear the stored Neo4j password through separate user destination consent and secure confirmation.
 * Steps: isolated committed API/stub; reject ordinary/unapproved password; replace/replay; refuse clear; stale account edit; self-heal; clear; optional browser.
 * Environment: actual compiled Native product, fresh owned data, two ephemeral loopback ports, optional pinned Chromium/new context.
 * Type: owned MemoryGraph HTTP control stub only; no real database, LLM, external Web, Runner execution or paid services.
 * Credentials: synthetic owned API/sidecar/password values; report only matching booleans and slots, redact logs.
 * CostSideEffects: own settings restored, exact own Project deleted; own process/browser/stub stopped.
 * Run: E2E_NEO4J_PASSWORD_ISOLATED=1 node test/api/configuration-neo4j-password.mjs
 * Browser: E2E_NEO4J_PASSWORD_BROWSER=1 and repository-pinned .e2e Chromium/cache.
 * Boundary: stored credentials and control-payload binding, not real Neo4j authentication, immediate runtime revocation or semantic model choice.
 */
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const directory = resolve(".tmp", "pr770-ui-run"); await mkdir(directory, { recursive: true });
const token = `owned-api-${randomUUID()}`, graphToken = `owned-sidecar-${randomUUID()}`;
// Distinct surrounding ASCII/Unicode whitespace must survive secure/API input.
const passwords = [0, 1, 2].map(() => ` \tsynthetic-neo4j-${randomUUID()}\u00a0 `);
const redact = value => [token, graphToken, ...passwords].reduce((text, secret) => text.replaceAll(secret, "[owned-secret]"), String(value));
const steps = [], requests = [], credentialPushes = [], browserUploads = [], ordinaryLeaks = [];
let outcome = "BLOCKED", sha, origin, graphOrigin, product, stub, project, session, baseline, browser, context, logs = "", graphBackend = "local", runtimeSlot = "cleared", expectedUser = "owned-user", featurePushes;
class Blocked extends Error {}
const git = (...args) => execFileSync("git", args, { encoding: "utf8" }).trim();
async function listen(server) { await new Promise(done => server.listen(0, "127.0.0.1", done)); return server.address().port; }
async function api(path, body, method = body === undefined ? "GET" : "POST", expected = body === undefined ? 200 : 201) {
  const response = await fetch(origin + path, { method, redirect: "error", signal: AbortSignal.timeout(15000),
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json", "Idempotency-Key": randomUUID() }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await response.json(); requests.push({ method, path, status: response.status, code: value.code });
  assert.equal(response.status, expected, `${method} ${path}: ${value.code ?? response.status}`);
  assert.ok(passwords.every(secret => !JSON.stringify(value).includes(secret)), "A password escaped into a public response"); return value;
}
async function step(name, work) {
  try { await work(); steps.push({ name, status: "PASS" }); }
  catch (error) { steps.push({ name, status: error instanceof Blocked ? "BLOCKED" : "FAIL", error: redact(error.message) }); throw error; }
}
const path = (card, action) => `/api/sessions/${session.id}/configuration-requests${card ? `/${card.id}` : ""}${action ? `/${action}` : ""}`;
const propose = operation => api(path(), { protocolVersion: 3, capabilityId: "memory.credential", values: { operation } });
const formal = () => api("/api/memory/settings");
const preview = (card, extra = {}) => api(path(card, "preview"), { expectedRevision: card.revision, values: { operation: "replace" }, selectedOptionId: "persist", ...extra }, "POST", 200);
const decide = (card, decision = "apply") => api(path(card, "decision"), { revision: card.revision, previewDigest: card.previewDigest, selectedOptionId: card.selectedOptionId, decision }, "POST", 200);
const consent = card => api(path(card, "endpoint-consent"), { revision: card.revision, endpointTarget: card.endpointAuthorization.endpointTarget }, "POST", 200);
async function fill(card, index) {
  const draft = await api(path(card, "credentials"), { expectedRevision: card.revision, fieldId: "apiToken", secret: passwords[index] }, "POST", 200);
  return preview(card, { credentialHandle: draft.handle });
}
try {
  await step("1. Start the immutable product with an owned sidecar stub and fresh settings/session", async () => {
    if (process.env.E2E_NEO4J_PASSWORD_ISOLATED !== "1") throw new Blocked("Explicit owned isolation is required");
    sha = git("rev-parse", "HEAD"); assert.equal(sha, "595ea493ab62dda8372f3257fb54fc5f2f1b738d"); assert.equal(git("rev-parse", "HEAD^{tree}"), "3aa1eea1da91fa30ef1e703285d2e3463b5feedb"); if (git("status", "--porcelain", "--untracked-files=no")) throw new Blocked("Commit the candidate before testing");
    stub = createServer(async (request, response) => {
      let raw = ""; for await (const part of request) raw += part;
      response.setHeader("content-type", "application/json");
      if (request.url === "/health") return response.end(JSON.stringify({ status: "degraded", backend: graphBackend }));
      if (request.headers.authorization !== `Bearer ${graphToken}`) { response.statusCode = 401; return response.end("{}"); }
      const body = JSON.parse(raw || "{}");
      if (request.url?.startsWith("/subgraph")) return response.end(JSON.stringify({ nodes: [], edges: [], total: 0, truncated: false }));
      if (request.url === "/internal/backend") graphBackend = body.backend;
      if (request.url === "/internal/neo4j-password") {
        runtimeSlot = body.password === null ? "cleared" : String(passwords.indexOf(body.password));
        credentialPushes.push({ slot: runtimeSlot, targetMatches: body.http_uri === graphOrigin && body.user === expectedUser });
      }
      return response.end(JSON.stringify({ ok: true }));
    });
    graphOrigin = `http://127.0.0.1:${await listen(stub)}`;
    const reservation = createServer(), port = await listen(reservation); await new Promise(done => reservation.close(done)); origin = `http://127.0.0.1:${port}`;
    const env = { ...process.env, SCIENCE_AGENT_HOST: "127.0.0.1", SCIENCE_AGENT_PORT: String(port), SCIENCE_AGENT_AUTH_TOKEN: token,
      SCIENCE_AGENT_DATA_DIR: resolve(directory, "data"), SCIENCE_DISCOVERY_DATA_DIR: resolve(directory, "data"), SCIENCE_AGENT_SAMPLE_SESSION: "0", SCIENCE_AGENT_EXECUTOR: "native",
      SCIENCE_AGENT_MEMORY_GRAPH_AVAILABLE: "1", SCIENCE_AGENT_MEMORY_GRAPH_URL: graphOrigin, SCIENCE_AGENT_MEMORY_GRAPH_INTERNAL_TOKEN: graphToken, SCIENCE_AGENT_RUNNER_URL: "http://127.0.0.1:1" };
    for (const name of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]) env[name] = "";
    product = spawn(process.execPath, ["services/api/dist/server.js"], { env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    for (const stream of [product.stdout, product.stderr]) stream.on("data", data => { logs += redact(data); });
    let ready = false; const deadline = Date.now() + 45000;
    while (Date.now() < deadline) { if (product.exitCode !== null) throw new Blocked("Owned product exited during startup");
      try { await api("/health"); ready = true; break; } catch {} await new Promise(done => setTimeout(done, 200)); }
    if (!ready) throw new Blocked("Owned product did not become ready");
    baseline = await formal(); if (baseline.hasNeo4jPassword) throw new Blocked("Fresh owned data must not contain real credentials");
    await api("/api/memory/settings", { enabled: false, backend: "neo4j", neo4jHttp: graphOrigin, neo4jUser: expectedUser }, "PUT", 200);
    project = await api("/api/projects", { name: `Neo4j password ${randomUUID()}` }); session = await api(`/api/projects/${project.id}/sessions`, { title: "Owned secure password" });
  });
  outcome = "FAIL";
  execFileSync(process.execPath, ["test/sync-e2e.mjs", "--check"]);
  const { chromium } = createRequire(resolve(".e2e/package.json"))("@playwright/test");
  browser = await chromium.launch({ headless: true, channel: "chromium" });
  const captures = []; const evidence = resolve(".tmp", "pr770-ui-evidence-output"); await mkdir(evidence, {recursive:true});
  for (const locale of ["en", "zh-CN"]) {
    context = await browser.newContext({ viewport: { width: 1440, height: 1800 }, deviceScaleFactor: 1 });
    await context.route("**/*", route => { const url = new URL(route.request().url()); return ["http:", "https:"].includes(url.protocol) && url.origin !== origin ? route.abort() : route.continue(); });
    await context.addInitScript(({ token, origin, locale }) => { if (location.origin === origin) { localStorage.setItem("sciencediscovery-token", token); localStorage.setItem("sciencediscovery-locale", locale); } }, { token, origin, locale });
    const m = locale === "en" ? { allow: "Allow this exact endpoint", preview: "Refresh proposed changes", confirm: "Confirm choice and continue" } : { allow: "允许此精确端点", preview: "刷新修改方案", confirm: "确认选择并继续" };
    const page = await context.newPage();
    page.on("request", request => { if (request.method() !== "POST") return; const body = request.postData() || ""; if (!request.url().endsWith("/credentials") && passwords.some(secret => body.includes(JSON.stringify(secret).slice(1,-1)))) ordinaryLeaks.push(true); });
    async function capture(name, locator) {
      await locator.scrollIntoViewIfNeeded(); await page.waitForTimeout(250);
      assert.ok(passwords.every(secret => !(awaitText.includes(secret))));
      const filename = resolve(evidence, `${locale}-${name}.png`);
      await locator.screenshot({ path: filename, animations: "disabled", caret: "hide" });
      captures.push({ locale, name, filename, text: await locator.innerText() });
    }
    let awaitText = "";
    async function snap(name, locator) { awaitText = await page.locator("body").innerText(); await capture(name, locator); }
    async function open(capabilityId, values) {
      session = await api(`/api/projects/${project.id}/sessions`, { title: `${locale} ${capabilityId} evidence` });
      const card = await api(path(), { protocolVersion: 3, capabilityId, values });
      await page.goto(`${origin}/projects/${project.id}/sessions/${session.id}`, { waitUntil: "domcontentloaded" });
      const panel = page.locator(`#configuration-request-${card.id}`); await panel.waitFor({ state: "visible" }); return panel;
    }
    await step(`${locale}: 2. Credential destination consent, masked input, write-only preview, manual save and restart notice`, async () => {
      await api("/api/memory/settings", { enabled: false, backend: "neo4j", neo4jHttp: graphOrigin, neo4jUser: expectedUser, neo4jPassword: null }, "PUT", 200);
      const panel = await open("memory.credential", { operation: "replace" });
      await snap("credential-before-consent", panel);
      assert.equal(await panel.locator('input[type="password"]').count(), 0);
      await panel.getByRole("button", { name: m.allow, exact: true }).click();
      const password = panel.locator('input[type="password"]'); await password.waitFor({state:"visible"});
      await password.fill(passwords[2]);
      await snap("credential-masked-input", panel);
      await panel.getByRole("button", { name: m.preview, exact: true }).click();
      await page.waitForFunction(() => { const e = document.querySelector('input[type="password"]'); return e && e.value === ""; });
      assert.equal((await formal()).hasNeo4jPassword, false);
      assert.equal(await panel.getByRole("button", { name: m.confirm, exact: true }).isEnabled(), true);
      await snap("credential-approved-manual-save-needed", panel);
      await panel.getByRole("button", { name: m.confirm, exact: true }).click();
      await panel.waitFor({state:"detached"}); assert.equal((await formal()).hasNeo4jPassword, true);
      const record = page.locator(".conversation-configuration-record").last(); await record.locator("summary").first().click();
      await snap("credential-saved-restart-required", record);
    });
    await step(`${locale}: 3. Connection recipient before and after consent, manual save and restart notice`, async () => {
      const panel = await open("memory.connection", { neo4jHttp: graphOrigin, neo4jUser: "approved-local-test-user" });
      await snap("connection-before-consent", panel);
      assert.equal(await panel.getByRole("button", {name:m.confirm, exact:true}).isEnabled(), false);
      await panel.getByRole("button", {name:m.allow, exact:true}).click();
      await panel.getByRole("button", {name:m.confirm, exact:true}).waitFor();
      assert.equal((await formal()).neo4jUser, expectedUser);
      await snap("connection-approved-manual-save-needed", panel);
      await panel.getByRole("button", {name:m.confirm, exact:true}).click(); await panel.waitFor({state:"detached"});
      assert.equal((await formal()).neo4jUser, "approved-local-test-user");
      const record = page.locator(".conversation-configuration-record").last(); await record.locator("summary").first().click();
      await snap("connection-saved-restart-required", record);
    });
    await step(`${locale}: 4. Backend switch and honest readiness status`, async () => {
      await api("/api/memory/settings", { enabled: true, backend: "local" }, "PUT", 200);
      const panel = await open("memory.backend", { backend: "neo4j" });
      await snap("backend-before-save", panel);
      await panel.getByRole("button", {name:m.confirm, exact:true}).click(); await panel.waitFor({state:"detached"});
      assert.equal((await formal()).backend, "neo4j");
      const record = page.locator(".conversation-configuration-record").last(); await record.locator("summary").first().click();
      await snap("backend-saved-readiness-unverified", record);
    });
    await context.close(); context = undefined;
  }
  assert.equal(ordinaryLeaks.length, 0);
  await writeFile(resolve(evidence,"captures.json"), JSON.stringify({ sha, tree:git("rev-parse","HEAD^{tree}"), chromium:await browser.version(), viewport:{width:1440,height:1800}, deviceScaleFactor:1, boundary:"Actual product at exact target SHA; synthetic owned secrets and loopback MemoryGraph control stub; no real Neo4j authentication or model calls. Saving is not connectivity proof.", ordinaryLeaks:ordinaryLeaks.length, captures },null,2));
  featurePushes = structuredClone(credentialPushes); outcome = "PASS";
} catch (error) { console.error(redact(error.stack)); outcome = error instanceof Blocked ? "BLOCKED" : "FAIL"; process.exitCode = 1; }
finally {
  await context?.close(); await browser?.close();
  try { if (baseline) await api("/api/memory/settings", { enabled: baseline.enabled, backend: baseline.backend, neo4jHttp: baseline.neo4jHttp, neo4jUser: baseline.neo4jUser, neo4jPassword: null }, "PUT", 200);
    if (project) await api(`/api/projects/${project.id}`, { confirmationId: project.id }, "DELETE", 200);
  } catch { outcome = "FAIL"; process.exitCode = 1; steps.push({ name: "Restore exact owned fixtures", status: "FAIL" }); }
  if (product && product.exitCode === null && product.signalCode === null) { product.kill(); await Promise.race([new Promise(done => product.once("exit", done)), new Promise(done => setTimeout(done, 5000))]); }
  if (stub) await new Promise(done => { stub.close(done); stub.closeAllConnections(); });
  if (product && product.exitCode === null && product.signalCode === null) { outcome = "FAIL"; process.exitCode = 1; }
  await writeFile(resolve(directory, "startup.log"), logs);
  await writeFile(resolve(directory, "report.json"), JSON.stringify({ outcome, sha, steps, requests, featurePushes, includingOwnedCleanup: credentialPushes, browserUploads, ordinaryLeaks,
    browser: "Repository-pinned Playwright Chromium; en and zh-CN; see step outcomes",
    boundary: "Owned MemoryGraph control stub, no real Neo4j/model/authentication or immediate runtime revocation", serverExit: { code: product?.exitCode, signal: product?.signalCode, pid: product?.pid } }, null, 2));
  await writeFile(resolve(directory, "report.md"), `# Neo4j password: ${outcome}\n\nCommit: ${sha ?? "unknown"}\n\n${steps.map(item => `- ${item.name}: ${item.status}${item.error ? ` — ${item.error}` : ""}`).join("\n")}\n`);
  await mkdir(resolve(".tmp", "pr770-ui-evidence-output"), {recursive:true});
  await writeFile(resolve(".tmp", "pr770-ui-evidence-output", "result.json"), JSON.stringify({outcome,sha,steps,boundary:"Isolated local mock, not live Neo4j verification",serverExited:product?.exitCode !== null || product?.signalCode !== null},null,2));
  console.log(JSON.stringify({ outcome, sha, report: resolve(directory, "report.md") }));
}
