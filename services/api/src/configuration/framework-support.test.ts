// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { createTest } from "../../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "os:windows", "arch:amd64", "arch:arm64"] });
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { rm } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { SessionStore } from "../store.js";
import { ConversationalConfigurationFramework } from "./framework.js";

test("CFG-T26 Native generic Web same-run proposal fails before creating either request or changing configuration", async context => {
  const parent = resolve(process.cwd(), ".tmp");
  const root = resolve(parent, `configuration-framework-support-${randomUUID()}`);
  const store = new SessionStore(root);
  context.after(async () => {
    store.close(); assert.ok(root.startsWith(parent + sep));
    await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  });
  await store.load();
  const project = await store.createProject("Configuration support regression");
  const session = await store.createSession(project.id, "Unsupported Web activation", {}, {}, { allowUnconfiguredModel: true });
  const run = await store.createSessionRun({ sessionId: session.id, prompt: "Use the built-in Web tool",
    settingsSnapshot: store.resolveRuntimeSettings(session.id).effective });
  const framework = new ConversationalConfigurationFramework(store, { nativeExecutor: true,
    connector: () => ({ name: "Web", ready: true }) });
  const before = structuredClone(store.getSessionSettings(session.id));
  await assert.rejects(framework.propose(session.id, { capabilityId: "connector.enable", values: { connectorId: "web" }, runId: run.id }),
    { code: "CONFIGURATION_ACTIVATION_UNAVAILABLE", status: 409 });
  assert.deepEqual(framework.repository.list(session.id), [], "No generic card can promise unsupported hot activation");
  assert.deepEqual(store.listConnectorConfigurationRequests(session.id), [], "No hidden legacy companion is created");
  assert.deepEqual(store.getSessionSettings(session.id), before, "No formal settings are changed before capability admission");
  assert.equal((await store.getSessionRun(session.id, run.id))!.status, "queued");
});
