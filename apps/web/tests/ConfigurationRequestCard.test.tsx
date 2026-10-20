// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");

import { createTest } from "../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "os:windows", "os:macos", "arch:amd64", "arch:arm64"] });
import assert from "node:assert/strict";
import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ConfigurationRequestView } from "@sciencediscovery/schema";
import { MODEL_API_VARIANTS, DEFAULT_MODEL_API_VARIANT, type ModelApiProtocol } from "@sciencediscovery/schema";
import { ConfigurationRequestCard, type ConfigurationRequestActions } from "../src/configuration/ConfigurationRequestCard.js";
import { ApiRequestError } from "../src/api/auth.js";
import { LocaleProvider } from "../src/i18n/index.js";
import { QuickConfigurationMenu } from "../src/configuration/QuickConfigurationMenu.js";
import { genericConfigurationPresentation } from "../src/configuration/presentation.js";
import type { ChatMessage } from "@sciencediscovery/schema";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function request(overrides: Partial<ConfigurationRequestView> = {}): ConfigurationRequestView {
  return {
    id: "cfg-1", projectId: "p1", sessionId: "s1", runId: "r1", revision: 2, stateVersion: 3, schemaVersion: 1, adapterVersion: 1,
    capabilityId: "model.select", state: "awaiting_confirmation", reason: { code: "MODEL_REQUIRED", message: "A different model is needed." },
    scope: { kind: "session", id: "s1" }, fields: [{ id: "modelId", label: "Task model", kind: "enum", required: true,
      choices: [{ id: "a", label: "Model A" }, { id: "b", label: "Model B" }], value: "a" }],
    changes: [{ field: "Task model", before: "Old model", after: "Model A" }],
    impact: { resourceLocation: "Model registry", effectiveScope: "This session only", message: "Other sessions keep their current model." },
    verificationPlan: { targets: [], actions: [], maxRequests: 0, mayCharge: false }, previewDigest: "sha256:preview-2",
    activation: { mode: "same_run", state: "not_requested" }, resumption: { state: "not_requested" },
    allowedActions: ["preview", "apply", "dismiss"], createdAt: "2026-10-19T00:00:00Z", updatedAt: "2026-10-19T00:00:00Z", ...overrides,
  };
}
function actions(overrides: Partial<ConfigurationRequestActions> = {}): ConfigurationRequestActions {
  return { preview: async () => request(), credential: async () => ({ handle: "opaque", expiresAt: "2099-01-01T00:00:00Z" }),
    decide: async () => request({ state: "applied", allowedActions: [] }), query: async () => request(),
    continue: async () => request(), reversal: async () => request(), ...overrides };
}
async function mount(view: ConfigurationRequestView, api = actions(), onUpdate: (value: ConfigurationRequestView) => void = () => undefined, locale: "en" | "zh" = "en") {
  let renderer: ReactTestRenderer;
  await act(async () => { renderer = create(createElement(LocaleProvider, { initialLocale: locale }, createElement(ConfigurationRequestCard, {
    request: view, actions: api, onUpdate,
  }))); });
  return renderer!;
}
const button = (renderer: ReactTestRenderer, text: string) => renderer.root.findAllByType("button").find((item) => item.children.join("") === text)!;

test("CFG generic renderer requires a new preview after edits and binds approval to the exact revision and digest", async () => {
  const decisions: unknown[] = [];
  const previews: unknown[] = [];
  const updates: ConfigurationRequestView[] = [];
  const renderer = await mount(request(), actions({
    preview: async (input) => { previews.push(input); return request({ revision: 3, previewDigest: "sha256:preview-3" }); },
    decide: async (input) => { decisions.push(input); return request({ state: "applied" }); },
  }), (value) => updates.push(value));
  assert.deepEqual(decisions, []);
  assert.match(JSON.stringify(renderer.toJSON()), /This session only/);
  await act(async () => { renderer.root.findByType("select").props.onChange({ target: { value: "b" } }); });
  assert.equal(button(renderer, "Confirm this change").props.disabled, true);
  await act(async () => { renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  assert.deepEqual(previews, [{ expectedRevision: 2, values: { modelId: "b" } }]);
  assert.equal(updates[0]?.previewDigest, "sha256:preview-3");
  // Parent has not supplied the new snapshot yet: do not approve the old preview.
  assert.equal(button(renderer, "Confirm this change").props.disabled, true);
  await act(async () => { renderer.update(createElement(ConfigurationRequestCard, { request: request({ revision: 3, previewDigest: "sha256:preview-3" }),
    actions: actions({ decide: async (input) => { decisions.push(input); return request({ state: "applied" }); } }), onUpdate: () => undefined })); });
  await act(async () => { button(renderer, "Confirm this change").props.onClick(); });
  assert.deepEqual(decisions, [{ revision: 3, previewDigest: "sha256:preview-3", decision: "apply" }]);
  await act(async () => { renderer.unmount(); });
});

test("CFG credentials are sent only through the dedicated channel and are cleared before preview", async () => {
  const secret = "test-secret-never-in-a-snapshot";
  const credentials: unknown[] = [];
  const previews: unknown[] = [];
  const updates: unknown[] = [];
  const renderer = await mount(request({ capabilityId: "model.repair_credential", fields: [
    { id: "apiKey", label: "API key", kind: "credential", required: true, status: "missing" },
  ] }), actions({
    credential: async (input) => { credentials.push(input); return { handle: "draft-handle", expiresAt: "2099-01-01T00:00:00Z" }; },
    preview: async (input) => { previews.push(input); return request({ revision: input.expectedRevision + 1 }); },
  }), (value) => updates.push(value));
  await act(async () => { renderer.root.findByType("input").props.onChange({ target: { value: secret } }); });
  await act(async () => { renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  assert.deepEqual(credentials, [{ expectedRevision: 3, fieldId: "apiKey", secret }]);
  assert.deepEqual(previews, [{ expectedRevision: 2, values: {} }, { expectedRevision: 3, values: {}, credentialHandle: "draft-handle" }]);
  assert.equal(renderer.root.findByType("input").props.value, "");
  assert.doesNotMatch(JSON.stringify(updates), /test-secret|draft-handle/);
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /test-secret|draft-handle/);
  await act(async () => { renderer.unmount(); });
});

test("CFG conflicts block old approvals and never echo raw credential-bearing errors", async () => {
  let reads = 0;
  const renderer = await mount(request(), actions({
    decide: async () => { throw new ApiRequestError("provider returned test-secret", 409, "STALE_CONFIGURATION_PREVIEW"); },
    query: async () => { reads++; return request({ state: "stale", allowedActions: ["preview", "query"] }); },
  }));
  await act(async () => { button(renderer, "Confirm this change").props.onClick(); });
  assert.equal(button(renderer, "Confirm this change").props.disabled, true);
  assert.match(JSON.stringify(renderer.toJSON()), /Refresh it/);
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /test-secret/);
  await act(async () => { button(renderer, "Refresh current state").props.onClick(); });
  assert.equal(reads, 1);
  await act(async () => { renderer.unmount(); });
});

test("CFG saved records collapse with separate activation and continuation results and retain disclosure", async () => {
  const renderer = await mount(request({ state: "applied", activation: { mode: "same_run", state: "active" },
    resumption: { state: "failed" }, allowedActions: ["continue"] }));
  assert.equal(renderer.root.findByType("details").props.open, false);
  assert.equal(renderer.root.findAllByType("input").length, 0);
  const content = JSON.stringify(renderer.toJSON());
  assert.match(content, /Saved/);
  assert.match(content, /Verified active/);
  assert.match(content, /Continuation failed/);
  await act(async () => { const target = { open: true }; renderer.root.findByType("details").props.onToggle({ target, currentTarget: target }); });
  assert.equal(renderer.root.findByType("details").props.open, true);
  assert.ok(button(renderer, "Retry activation / continuation"));
  await act(async () => { renderer.unmount(); });
});

test("CFG needs-fix inputs use registered labels, safe help links, and Chinese action text", async () => {
  const renderer = await mount(request({ state: "needs_fix", allowedActions: ["preview", "dismiss"], fields: [
    { id: "baseUrl", label: "API 地址", kind: "url", required: true, value: "https://api.example.test", description: "服务地址", help: { label: "帮助", url: "javascript:alert(1)" } },
  ], error: { code: "NETWORK_UNREACHABLE", message: "无法连接模型服务", field: "baseUrl", retryable: true } }), actions(), () => undefined, "zh");
  const input = renderer.root.findByType("input");
  assert.equal(input.props.type, "url");
  assert.equal(input.props["aria-invalid"], true);
  assert.equal(renderer.root.findByType("label").props.htmlFor, input.props.id);
  assert.equal(renderer.root.findAllByType("a").length, 0);
  assert.ok(button(renderer, "更新预览"));
  assert.match(JSON.stringify(renderer.toJSON()), /需要修正/);
  await act(async () => { renderer.unmount(); });
});

test("CFG leaving the card during target preview prevents a later credential upload", async () => {
  let finishTarget!: (value: ConfigurationRequestView) => void;
  const target = new Promise<ConfigurationRequestView>((resolve) => { finishTarget = resolve; });
  let uploads = 0;
  const renderer = await mount(request({ fields: [{ id: "apiToken", label: "API key", kind: "credential", required: true, status: "missing" }] }),
    actions({ preview: async () => target, credential: async () => { uploads++; return { handle: "unused", expiresAt: "2099-01-01T00:00:00Z" }; } }));
  await act(async () => { renderer.root.findByType("input").props.onChange({ target: { value: "do-not-send-after-leaving" } }); });
  await act(async () => { renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  await act(async () => { renderer.unmount(); });
  await act(async () => { finishTarget(request({ revision: 3 })); await target; });
  assert.equal(uploads, 0);
});

test("CFG model protocol changes use the existing model metadata to update the variant before preview", async () => {
  const [first, second] = Object.keys(MODEL_API_VARIANTS) as ModelApiProtocol[];
  const previews: unknown[] = [];
  const renderer = await mount(request({ capabilityId: "model.connect", fields: [
    { id: "apiProtocol", kind: "enum", label: "Protocol", required: true, value: first!, choices: Object.keys(MODEL_API_VARIANTS).map((id) => ({ id, label: id })) },
    { id: "apiVariant", kind: "enum", label: "Variant", required: true, value: DEFAULT_MODEL_API_VARIANT[first!], choices: MODEL_API_VARIANTS[first!].map((id) => ({ id, label: id })) },
  ] }), actions({ preview: async (input) => { previews.push(input); return request({ revision: 3 }); } }));
  await act(async () => { renderer.root.findByProps({ name: "apiProtocol" }).props.onChange({ target: { value: second! } }); });
  assert.equal(renderer.root.findByProps({ name: "apiVariant" }).props.value, DEFAULT_MODEL_API_VARIANT[second!]);
  await act(async () => { renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  assert.deepEqual(previews, [{ expectedRevision: 2, values: { apiProtocol: second, apiVariant: DEFAULT_MODEL_API_VARIANT[second!] } }]);
  await act(async () => { renderer.unmount(); });
});

test("CFG quick configuration entry never creates a request until explicitly chosen", async () => {
  const choices: string[] = [];
  let renderer: ReactTestRenderer;
  await act(async () => { renderer = create(createElement(QuickConfigurationMenu, { disabled: false, onRequest: async (capability, key) => {
    assert.ok(key.length > 10); choices.push(capability);
  } })); });
  assert.deepEqual(choices, []);
  assert.equal(renderer!.root.findAllByType("button").length, 7);
  await act(async () => { button(renderer!, "Repair model credentials").props.onClick(); });
  assert.deepEqual(choices, ["model.repair_credential"]);
  await act(async () => { renderer!.unmount(); });
});

function choiceRequest(overrides: Partial<ConfigurationRequestView> = {}): ConfigurationRequestView {
  return request({ protocolVersion: 3, capabilityId: "connector.enable", selectedOptionId: "save", fields: [], options: [
    { id: "save", kind: "persisted_apply", label: "Save for this session", description: "Future tasks can use PubMed.", available: true },
    { id: "once", kind: "run_overlay", label: "Use for this task", description: "Session settings stay unchanged.", available: true },
    { id: "web", kind: "alternative", label: "Use Web search", description: "Use the existing Web tool.", available: true },
    { id: "dismiss", kind: "dismiss", label: "Do not configure", description: "Return the choice to the task.", available: true },
  ], ...overrides });
}

test("CFG-T30 option changes preview on the server before allowing the exact selected choice", async () => {
  const previews: unknown[] = [];
  const decisions: unknown[] = [];
  const updates: ConfigurationRequestView[] = [];
  const next = choiceRequest({ revision: 3, selectedOptionId: "once", previewDigest: "sha256:once" });
  const api = actions({ preview: async input => { previews.push(input); return next; }, decide: async input => { decisions.push(input); return choiceRequest({ state: "applied" }); } });
  const renderer = await mount(choiceRequest(), api, value => updates.push(value));
  const once = renderer.root.findAllByType("input").find(input => input.props.type === "radio" && input.props.value === "once")!;
  await act(async () => { once.props.onChange(); });
  assert.deepEqual(previews, [{ expectedRevision: 2, values: {}, selectedOptionId: "once" }]);
  assert.equal(button(renderer, "Confirm choice and continue").props.disabled, true);
  assert.equal(updates[0]?.selectedOptionId, "once");
  await act(async () => renderer.update(createElement(ConfigurationRequestCard, { request: next, actions: api, onUpdate() {} })));
  await act(async () => { button(renderer, "Confirm choice and continue").props.onClick(); });
  assert.deepEqual(decisions, [{ revision: 3, previewDigest: "sha256:once", decision: "apply", selectedOptionId: "once" }]);
  await act(async () => renderer.unmount());
});

test("CFG-T32 explicit close previews refusal and returns it; unmount does not decide", async () => {
  const calls: unknown[] = [];
  const api = actions({ preview: async input => { calls.push(input); return choiceRequest({ revision: 3, selectedOptionId: "dismiss", previewDigest: "sha256:dismiss" }); },
    decide: async input => { calls.push(input); return choiceRequest({ state: "dismissed" }); } });
  const renderer = await mount(choiceRequest(), api);
  await act(async () => { renderer.root.findAllByType("button").find(button => button.props["aria-label"] === "Close without configuring and return to the task")!.props.onClick(); });
  assert.deepEqual(calls, [
    { expectedRevision: 2, values: {}, selectedOptionId: "dismiss" },
    { revision: 3, previewDigest: "sha256:dismiss", decision: "dismiss", selectedOptionId: "dismiss" },
  ]);
  await act(async () => renderer.unmount());
  const idle = await mount(choiceRequest(), api);
  await act(async () => idle.unmount());
  assert.equal(calls.length, 2);
});

test("CFG-T31/32 collapsed receipts distinguish temporary use and alternatives from saved settings", async () => {
  for (const kind of ["run_overlay", "alternative"] as const) {
    const renderer = await mount(choiceRequest({ state: kind === "alternative" ? "dismissed" : "applied", allowedActions: [],
      choiceReceipt: { optionId: kind, kind, label: kind, configurationWritten: false, scope: kind === "run_overlay" ? "run" : "none" },
      resumption: { state: "started", runId: "r1" } }));
    const summary = renderer.root.findByType("summary");
    assert.doesNotMatch(summary.children.join(""), /Saved/);
    assert.match(JSON.stringify(renderer.toJSON()), /No persistent settings changed/);
    assert.equal(renderer.root.findAllByType("input").length, 0);
    await act(async () => renderer.unmount());
  }
});

test("CFG P1 saved next-run receipts describe future activation without implying an unfinished original task", async () => {
  const saved = choiceRequest({ capabilityId: "thinking.configure", state: "applied", runId: undefined, allowedActions: [],
    choiceReceipt: { optionId: "save", kind: "persisted_apply", label: "Save configuration", configurationWritten: true, scope: "session" },
    activation: { mode: "next_run", state: "needs_user_action" }, resumption: { state: "not_requested" } });
  const renderer = await mount(saved, actions(), () => undefined, "zh");
  const label = () => renderer.root.findByProps({ className: "record-label" }).children.join("");
  assert.equal(label(), "调整思考配置 · 已保存 · 后续运行生效");
  assert.equal(renderer.root.findByType("details").props.open, false);
  assert.match(JSON.stringify(renderer.toJSON()), /等待后续运行/);
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /需要用户处理/);
  await act(async () => renderer.update(createElement(LocaleProvider, { initialLocale: "zh" }, createElement(ConfigurationRequestCard, {
    request: { ...saved, activation: { mode: "requires_restart", state: "needs_user_action" } }, actions: actions(), onUpdate() {},
  }))));
  assert.match(JSON.stringify(renderer.toJSON()), /需要重启/);
  assert.match(JSON.stringify(renderer.toJSON()), /需要用户处理/);
  assert.doesNotMatch(label(), /后续运行生效/);
  await act(async () => renderer.update(createElement(LocaleProvider, { initialLocale: "zh" }, createElement(ConfigurationRequestCard, {
    request: { ...saved, runId: "r1", activation: { mode: "same_run", state: "active" }, resumption: { state: "started", runId: "r1" } },
    actions: actions(), onUpdate() {},
  }))));
  assert.equal(label(), "调整思考配置 · 保存配置后继续 · 原任务继续运行中");
  await act(async () => renderer.unmount());
});

test("CFG-T33 multiselect fields send only selected IDs through the preview values", async () => {
  const previews: unknown[] = [];
  const renderer = await mount(request({ capabilityId: "skill.select", fields: [{ id: "enabledSkillIds", label: "Skills", kind: "multiselect", required: false,
    value: ["skill-a"], choices: [{ id: "skill-a", label: "A" }, { id: "skill-b", label: "B" }] }] }), actions({ preview: async input => { previews.push(input); return request(); } }));
  await act(async () => renderer.root.findByType("select").props.onChange({ target: { selectedOptions: [{ value: "skill-b" }] } }));
  await act(async () => renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }));
  assert.deepEqual(previews, [{ expectedRevision: 2, values: { enabledSkillIds: ["skill-b"] } }]);
  await act(async () => renderer.unmount());
});

test("CFG stopped and archived decisions block mutations rather than suggesting a stale-preview retry", async () => {
  for (const code of ["SESSION_NOT_WRITABLE", "CONTINUATION_STOPPED"]) {
    const renderer = await mount(request(), actions({ decide: async () => { throw new ApiRequestError("raw-provider-secret", 409, code); } }));
    await act(async () => { button(renderer, "Confirm this change").props.onClick(); });
    assert.equal(button(renderer, "Confirm this change").props.disabled, true);
    assert.equal(button(renderer, "Update preview").props.disabled, true);
    assert.equal(button(renderer, "Decline").props.disabled, true);
    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /raw-provider-secret|preview changed/);
    assert.match(JSON.stringify(renderer.toJSON()), code === "CONTINUATION_STOPPED" ? /original task was stopped/ : /read-only/);
    await act(async () => { renderer.unmount(); });
  }
});

test("CFG invalid fields retain other input and identify the field without echoing a server diagnostic", async () => {
  const renderer = await mount(request({ capabilityId: "model.connect", fields: [
    { id: "baseUrl", label: "Endpoint", kind: "url", required: true, value: "https://keep-input.test" },
    { id: "name", label: "Name", kind: "text", required: true, value: "Keep this name" },
  ] }), actions({ preview: async () => { throw new ApiRequestError("do-not-echo", 400, "INVALID_CONFIGURATION_INPUT", { field: "baseUrl" }); } }));
  await act(async () => { renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  assert.equal(renderer.root.findByProps({ name: "baseUrl" }).props["aria-invalid"], true);
  assert.equal(renderer.root.findByProps({ name: "name" }).props.value, "Keep this name");
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /do-not-echo/);
  await act(async () => { renderer.unmount(); });
});

test("CFG a missing request removes the obsolete card's actions through the owning session callback", async () => {
  let removed = 0;
  let renderer: ReactTestRenderer;
  await act(async () => { renderer = create(createElement(ConfigurationRequestCard, { request: request(), actions: actions({
    decide: async () => { throw new ApiRequestError("not found", 404, "CONFIGURATION_REQUEST_NOT_FOUND"); },
  }), onUpdate: () => undefined, onUnavailable: () => { removed++; } })); });
  await act(async () => { button(renderer!, "Confirm this change").props.onClick(); });
  assert.equal(removed, 1);
  assert.equal(button(renderer!, "Confirm this change").props.disabled, true);
  await act(async () => { renderer!.unmount(); });
});

test("CFG credential double submission creates only one draft and Escape returns menu focus", async () => {
  let finishTarget!: (value: ConfigurationRequestView) => void;
  let uploads = 0;
  const target = new Promise<ConfigurationRequestView>((resolve) => { finishTarget = resolve; });
  const renderer = await mount(request({ fields: [{ id: "apiToken", label: "Key", kind: "credential", required: true, status: "missing" }] }), actions({
    preview: async (input) => input.credentialHandle ? request({ revision: 4 }) : target,
    credential: async () => { uploads++; return { handle: "single-draft", expiresAt: "2099-01-01T00:00:00Z" }; },
  }));
  await act(async () => { renderer.root.findByType("input").props.onChange({ target: { value: "credential" } }); });
  await act(async () => {
    const submit = renderer.root.findByType("form").props.onSubmit;
    submit({ preventDefault() {} }); submit({ preventDefault() {} });
  });
  await act(async () => { finishTarget(request({ revision: 3 })); await target; });
  assert.equal(uploads, 1);
  await act(async () => { renderer.unmount(); });

  let focusCount = 0;
  const element = { open: true, querySelector: () => ({ focus: () => { focusCount++; } }) };
  let menu: ReactTestRenderer;
  await act(async () => { menu = create(createElement(QuickConfigurationMenu, { disabled: false, onRequest: async () => undefined }), {
    createNodeMock: (node) => node.type === "details" ? element : null,
  }); });
  await act(async () => { menu!.root.findByType("details").props.onKeyDown({ key: "Escape" }); });
  assert.equal(element.open, false);
  assert.equal(focusCount, 1);
  assert.equal(menu!.root.findAllByType("button").every((item) => item.props.type === "button"), true,
    "Menu actions must not submit the surrounding research composer");
  await act(async () => { menu!.unmount(); });
});

test("CFG resolved generic requests move to their persisted notice without a second composer or legacy receipt", () => {
  const saved = request({ state: "applied" });
  const pending = request({ id: "pending", state: "awaiting_confirmation" });
  const foreign = request({ id: "foreign", sessionId: "other-session", state: "applied" });
  const notices = new Map([["notice", { requestId: saved.id }], ["duplicate", { requestId: saved.id }], ["foreign-notice", { requestId: foreign.id }]]);
  const messages = ["notice", "duplicate", "foreign-notice"].map((id) => ({ id, kind: "configuration_notice" } as ChatMessage));
  const result = genericConfigurationPresentation([saved, pending, foreign], messages, notices, "s1");
  assert.equal(result.byNoticeId.get("notice")?.id, saved.id);
  assert.deepEqual(result.visibleRequests.map((item) => item.id), ["pending"]);
  assert.ok(result.suppressedNoticeIds.has("duplicate"));
  assert.equal(result.byNoticeId.has("foreign-notice"), false);
  const withoutNotice = genericConfigurationPresentation([saved], [], notices, "s1");
  assert.equal(withoutNotice.visibleRequests[0]?.id, saved.id, "Keep the collapsed record visible until its notice is persisted");
});

test("CFG generic pre-authorized card shows the server deadline and cancels through the versioned dismiss action", async () => {
  const decisions: unknown[] = [];
  const deadline = new Date(Date.now() + 30_000).toISOString();
  const renderer = await mount(request({ capabilityId: "connector.enable", autoApplyAt: deadline }), actions({ decide: async (input) => {
    decisions.push(input); return request({ state: "dismissed", decisionSource: "user", allowedActions: [] });
  } }));
  assert.match(JSON.stringify(renderer.toJSON()), /applies automatically in 30s/);
  assert.equal(renderer.root.findAllByType("time").some((node) => node.props.dateTime === deadline), true);
  assert.equal(renderer.root.findByType("select").props.disabled, true, "The scheduled preview stays fixed until cancelled");
  assert.deepEqual(decisions, [], "Rendering a countdown cannot approve the request");
  await act(async () => { button(renderer, "Cancel this change").props.onClick(); });
  assert.deepEqual(decisions, [{ revision: 2, previewDigest: "sha256:preview-2", decision: "dismiss" }]);
  await act(async () => { renderer.unmount(); });
});

test("CFG only server DTOs drive countdown removal and automatic decision provenance", async () => {
  const decisions: unknown[] = [];
  const expiredDeadline = new Date(Date.now() - 1_000).toISOString();
  const api = actions({ decide: async (input) => { decisions.push(input); return request(); } });
  const renderer = await mount(request({ capabilityId: "connector.enable", autoApplyAt: expiredDeadline }), api);
  assert.match(JSON.stringify(renderer.toJSON()), /Waiting for the server to review/);
  assert.deepEqual(decisions, [], "A browser at zero seconds must not call apply");
  await act(async () => { renderer.update(createElement(ConfigurationRequestCard, { request: request({ capabilityId: "connector.enable", stateVersion: 4 }), actions: api, onUpdate: () => undefined })); });
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /Scheduled server review|Waiting for the server to review|applies automatically/);
  assert.equal(renderer.root.findByType("select").props.disabled, false, "Removing the authorization reopens the ordinary preview controls");
  const updatedAt = "2026-10-19T15:01:00Z";
  await act(async () => { renderer.update(createElement(ConfigurationRequestCard, { request: request({ capabilityId: "connector.enable", state: "applied",
    decisionSource: "auto", stateVersion: 5, updatedAt, allowedActions: [] }), actions: api, onUpdate: () => undefined })); });
  assert.equal(renderer.root.findByType("details").props.open, false);
  assert.match(JSON.stringify(renderer.toJSON()), /Automatic · pre-authorized/);
  assert.match(JSON.stringify(renderer.toJSON()), /Last updated/);
  assert.equal(renderer.root.findAllByType("time").some((node) => node.props.dateTime === updatedAt), true);
  assert.deepEqual(decisions, []);
  await act(async () => { renderer.unmount(); });
});

test("CFG an expired same-target draft can be replaced through target preview, secure upload, and a fresh provided snapshot", async () => {
  // GET may still expose the previous provided status until the ordinary
  // preview detects expiry; neither representation can trap the user in a
  // retry loop or require the browser to recover the old opaque handle.
  for (const oldStatus of ["provided", "expired"] as const) {
    const freshSecret = `replacement-secret-${oldStatus}`;
    const freshHandle = `replacement-handle-${oldStatus}`;
    const expiredSecret = "expired-secret-must-not-be-reused";
    const expiredHandle = "expired-handle-must-not-be-reused";
    const calls: Array<{ operation: string; input: unknown }> = [];
    const updates: ConfigurationRequestView[] = [];
    const repairView = (revision: number, status: "provided" | "missing" | "expired", state: ConfigurationRequestView["state"]) => request({
      capabilityId: "model.repair_credential", scope: { kind: "global" }, revision, state,
      previewDigest: `sha256:repair-${revision}`, fields: [
        { id: "modelId", label: "Existing model", kind: "enum", required: true, value: "a", choices: [{ id: "a", label: "Model A" }] },
        { id: "apiToken", label: "Replacement key", kind: "credential", required: true, status },
      ], allowedActions: state === "awaiting_confirmation" ? ["preview", "apply", "dismiss"] : ["preview", "dismiss"],
    });
    const api = actions({
      preview: async (input) => {
        calls.push({ operation: "preview", input });
        if (calls.length === 1) {
          assert.equal(input.credentialHandle, undefined, "A normal target preview must not send an old handle");
          // Backend clears only the expired isolated draft, preserving the
          // authoritative model/provider credential, and returns a new basis.
          return repairView(9, "missing", "collecting_input");
        }
        assert.equal(input.credentialHandle, freshHandle);
        return repairView(10, "provided", "awaiting_confirmation");
      },
      credential: async (input) => {
        calls.push({ operation: "credential", input });
        return { handle: freshHandle, expiresAt: "2099-01-01T00:00:00Z" };
      },
      query: async () => { assert.fail("Replacing an expired isolated draft must not require a refresh loop"); },
    });
    let renderer: ReactTestRenderer;
    const draw = (view: ConfigurationRequestView) => createElement(ConfigurationRequestCard, { key: `${view.id}:${view.revision}`,
      request: view, actions: api, onUpdate: (next: ConfigurationRequestView) => { updates.push(next); renderer.update(draw(next)); } });
    await act(async () => { renderer = create(draw(repairView(8, oldStatus, "awaiting_confirmation"))); });
    await act(async () => { renderer!.root.findByProps({ name: "apiToken" }).props.onChange({ target: { value: freshSecret } }); });
    await act(async () => { renderer!.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
    assert.deepEqual(calls, [
      { operation: "preview", input: { expectedRevision: 8, values: { modelId: "a" } } },
      { operation: "credential", input: { expectedRevision: 9, fieldId: "apiToken", secret: freshSecret } },
      { operation: "preview", input: { expectedRevision: 9, values: { modelId: "a" }, credentialHandle: freshHandle } },
    ]);
    assert.doesNotMatch(JSON.stringify(calls), new RegExp(`${expiredSecret}|${expiredHandle}`));
    assert.equal(updates.length, 1);
    assert.equal(updates[0]?.revision, 10);
    assert.equal(updates[0]?.fields.find((field) => field.kind === "credential")?.status, "provided");
    assert.doesNotMatch(JSON.stringify(updates), new RegExp(`${freshSecret}|${freshHandle}|${expiredSecret}|${expiredHandle}`));
    const credentialInput = renderer!.root.findByProps({ name: "apiToken" });
    assert.equal(credentialInput.props.value, "");
    assert.match(credentialInput.props.placeholder, /Credential provided/);
    assert.match(JSON.stringify(renderer!.toJSON()), /Credential provided/);
    assert.equal(button(renderer!, "Confirm this change").props.disabled, false);
    await act(async () => { renderer!.unmount(); });
  }
});
