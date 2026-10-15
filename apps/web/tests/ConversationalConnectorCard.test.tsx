// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");

import { createTest } from "../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "arch:amd64", "arch:arm64"] });
import assert from "node:assert/strict";
import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ConnectorConfigurationRequest } from "@sciencediscovery/schema";
import { ConversationalConnectorCard } from "../src/configuration/ConversationalConnectorCard.js";
import { LocaleProvider } from "../src/i18n/index.js";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

test("CFG connector card waits for user confirmation and shows the session-only impact", async () => {
  const decisions: string[] = [];
  const request: ConnectorConfigurationRequest = {
    id: "request-1", sessionId: "session-1", projectId: "project-1", runId: "run-1",
    connectorId: "pubmed", connectorName: "PubMed", reason: "Needed for this task",
    baselineConnectorIds: [], state: "awaiting_confirmation", createdAt: "2026-10-15T00:00:00Z", updatedAt: "2026-10-15T00:00:00Z",
  };
  let renderer: ReactTestRenderer;
  await act(async () => {
    renderer = create(createElement(LocaleProvider, { initialLocale: "en" }, createElement(ConversationalConnectorCard, {
      request, runActive: false, onDecision: async (decision) => { decisions.push(decision); },
    })));
  });
  const rendered = JSON.stringify(renderer!.toJSON());
  assert.match(rendered, /Enable PubMed/);
  assert.match(rendered, /This session only; other sessions stay unchanged/);
  assert.deepEqual(decisions, []);
  await act(async () => {
    renderer!.root.findByProps({ className: "primary-button" }).props.onClick();
  });
  assert.deepEqual(decisions, ["apply"]);
});
