// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { createTest } from "../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "arch:amd64", "arch:arm64"] });
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ConfigurationRequestView } from "@sciencediscovery/schema";
import { ApiClient } from "../src/api/client.js";
import { emptyRunStream } from "../src/run-stream/model.js";
import { Conversation } from "../src/views/Conversation.js";
import { setLanguage } from "../src/text.js";

function request(overrides: Partial<ConfigurationRequestView> = {}): ConfigurationRequestView {
  return {
    id: "cfg", sessionId: "session", projectId: "project", revision: 1, stateVersion: 1, schemaVersion: 1, adapterVersion: 1,
    capabilityId: "model.select", state: "awaiting_confirmation", reason: { code: "MODEL_REQUIRED", message: "Review the model change" },
    scope: { kind: "session", id: "session" }, fields: [{ id: "modelId", label: "Task model", kind: "enum", required: true, choices: [{ id: "model", label: "Model" }], value: "model" }],
    changes: [{ field: "modelId", before: "old", after: "model" }],
    impact: { resourceLocation: "Model registry", effectiveScope: "This session", message: "No other sessions change" },
    verificationPlan: { targets: [], actions: [], maxRequests: 0, mayCharge: false }, previewDigest: "sha256:preview",
    activation: { mode: "next_run", state: "not_requested" }, resumption: { state: "not_requested" },
    allowedActions: ["preview", "apply", "dismiss"], createdAt: "2026-11-09T00:00:00Z", updatedAt: "2026-11-09T00:00:00Z", ...overrides,
  };
}
function render(options: { request?: ConfigurationRequestView; notices?: boolean; emptyNotice?: boolean; error?: boolean; readOnly?: boolean; run?: ReturnType<typeof emptyRunStream> } = {}) {
  const card = options.request ?? request();
  return renderToStaticMarkup(<Conversation client={new ApiClient("test")} loading={false} run={options.run ?? emptyRunStream()}
    detail={{ id: "session", messages: options.notices ? [
      { id: "notice-1", kind: "configuration_notice", configurationRequestId: "cfg", content: options.emptyNotice ? "" : "raw receipt one", role: "assistant", createdAt: card.updatedAt },
      { id: "notice-2", kind: "configuration_notice", configurationRequestId: "cfg", content: options.emptyNotice ? "" : "raw receipt two", role: "assistant", createdAt: card.updatedAt },
    ] : [] } as never} replays={[]} workspaceFiles={[]} onDecidePermission={async () => {}}
    configurationReadOnly={options.readOnly}
    configuration={{ requests: [card], legacyRequests: [], loading: false, error: options.error ?? false, refresh: async () => {}, update: () => {}, remove: () => {} }} />);
}

test("desktop shows a pending card even without a conversation message", () => {
  setLanguage("en");
  const html = render();
  assert.match(html, /id="configuration-request-cfg"/);
  assert.match(html, /Confirm this change/);
  assert.match(html, /No other sessions change/);
});
test("a standalone configuration does not mistake two missing run IDs for an active run", () => {
  setLanguage("en");
  const html = render();
  assert.doesNotMatch(html, /<button[^>]*disabled=""[^>]*>Refresh proposed changes/);
  assert.doesNotMatch(html, /<button[^>]*disabled=""[^>]*>Confirm this change/);
  assert.doesNotMatch(html, /Waiting for the current tool batch/);
});
test("a matching active source run blocks configuration until its safe pause boundary", () => {
  const run = { ...emptyRunStream(), runId: "run" };
  const pending = render({ request: request({ runId: "run" }), run });
  assert.match(pending, /<button[^>]*disabled=""[^>]*>Refresh proposed changes/);
  const paused = render({ request: request({ runId: "run", liveWait: true }), run });
  assert.doesNotMatch(paused, /<button[^>]*disabled=""[^>]*>Refresh proposed changes/);
});
test("resolved configuration renders one expandable receipt at its notice", () => {
  const html = render({ request: request({ state: "applied", allowedActions: ["query"] }), notices: true });
  assert.equal((html.match(/<details /g) ?? []).length, 1);
  assert.match(html, /desktop-configuration-record/);
  assert.match(html, /msg-collapse tool-card/);
  assert.match(html, /<span aria-hidden="true" class="collapse-chev">/);
  assert.match(html, /class="configuration-record-body"/);
  assert.doesNotMatch(html, /raw receipt one|raw receipt two|id="configuration-request-cfg"/);
});
test("pending cards replace their empty API anchors without a duplicate notice row", () => {
  setLanguage("en");
  const html = render({ notices: true, emptyNotice: true });
  assert.equal((html.match(/id="configuration-request-cfg"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /is-notice|configuration_notice|Configuration update/);
});
test("nonempty configuration notices keep their content and a localized label", () => {
  setLanguage("zh-CN");
  const html = render({ notices: true });
  assert.match(html, /raw receipt one/);
  assert.match(html, /配置更新/);
  assert.doesNotMatch(html, /configuration_notice/);
  setLanguage("en");
});
test("desktop preserves its selected language in the shared card", () => {
  setLanguage("zh-CN");
  assert.match(render(), /配置请求/);
  setLanguage("en");
  assert.match(render(), /Configuration request/);
});
test("a failed request refresh blocks writes and gives a retry without hiding the card", () => {
  setLanguage("en");
  const html = render({ error: true });
  assert.match(html, /id="configuration-request-cfg"/);
  assert.match(html, /could not refresh/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>Confirm this change/);
  assert.match(html, />Retry<\/button>/);
});
test("archived or evidence-only sessions cannot approve configuration changes", () => {
  const html = render({ readOnly: true });
  assert.match(html, /<button[^>]*disabled=""[^>]*>Confirm this change/);
  assert.match(html, /<select[^>]*disabled=""/);
});
