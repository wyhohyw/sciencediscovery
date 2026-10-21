// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { Type } from "typebox";
import type { AgentTool } from "@sciencediscovery/tools";
import type { ConfigurationCapabilityId, ModelProfile, SkillLibrary } from "@sciencediscovery/schema";
import type { SessionStore } from "../store.js";
import { configurationFramework } from "./framework.js";
import { ConfigurationError, object } from "./validation.js";
import { CONFIGURATION_SETTINGS_INVENTORY } from "./settings-catalog.js";
import { configurationDiagnostic } from "./diagnostics.js";
import { thinkingChoices } from "./p1-adapters.js";
import { nativeApprovedRunConnectorIds } from "./native-choices.js";

/** Main Agent only. Deliberately contains no decision, credential, file or command tool. */
export function createConfigurationAgentTools(input: { store: SessionStore; sessionId: string; runId: string }): AgentTool[] {
  const { store, sessionId, runId } = input;
  const framework = configurationFramework(store);
  const inspectParameters = Type.Object({ diagnosticCode: Type.Optional(Type.String({ maxLength: 100 })) }, { additionalProperties: false });
  const inspect: AgentTool<typeof inspectParameters> = {
    name: "inspect_configuration", label: "Inspect supported settings",
    description: "Read registered settings, task applicability, exact input fields, non-sensitive current values, inherited sources, installed resources and executor support. Optional structured diagnostic codes classify actual failures; free-text errors are never evidence. No endpoint probe, mutation or credentials.",
    parameters: inspectParameters,
    execute: async (_id, args) => {
      object(args, ["diagnosticCode"], "inspection");
      const session = store.assertSessionWritable(sessionId);
      const { effective: settings, sources } = store.getSessionSettings(sessionId);
      const snapshot = store.configurationSnapshot(sessionId, "skill.select", "skill.select");
      const run = framework.options.nativeExecutor ? await store.getSessionRun(sessionId, runId) : undefined;
      const runtimeEnabledConnectorIds = run ? nativeApprovedRunConnectorIds(store, run) : settings.enabledConnectorIds;
      return { content: [{ type: "text", text: JSON.stringify({ sessionId: session.id,
        capabilities: framework.service.registry.describe().map(capability => {
          const p1 = ["skill.select", "thinking.configure", "review.configure"].includes(capability.id);
          const unsupportedReason = p1 && !framework.options.nativeExecutor ? "JiuwenSwarm scoped host configuration is not integrated"
            : capability.id === "thinking.configure" && !thinkingChoices(snapshot.data.effectiveModel as ModelProfile | undefined).modes.length ? "The selected model API variant has no thinking control"
            : capability.id === "skill.select" && !snapshot.data.skillSelectionAvailable ? "This backend uses a shared Skill catalog" : undefined;
          return { ...capability, available: !unsupportedReason, ...(unsupportedReason ? { unsupportedReason } : {}) };
        }),
        modelId: settings.modelId ?? null,
        models: store.listModels().map(model => ({ id: model.id, name: model.name, credential: model.hasApiToken ? "provided" : store.modelAllowsMissingToken(model) ? "not_required" : "missing" })),
        current: { enabledConnectorIds: runtimeEnabledConnectorIds, sessionSavedEnabledConnectorIds: settings.enabledConnectorIds, skillSelectionMode: settings.skillSelectionMode,
          enabledSkillIds: settings.enabledSkillIds, enabledSkillLibraries: settings.enabledSkillLibraries,
          thinkingMode: settings.thinkingMode ?? "model_default", thinkingEffort: settings.thinkingEffort ?? "model_default",
          reviewModelId: settings.reviewModelId ?? settings.modelId ?? null, semanticReviewEnabled: settings.semanticReviewEnabled,
          reviewer: snapshot.data.sessionReviewer },
        sources, installedSkillIds: snapshot.data.installedSkillIds,
        skillLibraries: (snapshot.data.skillLibraries as SkillLibrary[]).map(item => ({ id: item.id, name: item.name, versionId: item.headVersionId, ready: Boolean(item.headVersionId) })),
        thinkingSupported: thinkingChoices(snapshot.data.effectiveModel as ModelProfile | undefined),
        reviewerGloballyEnabled: snapshot.data.reviewerGloballyEnabled,
        settingsInventory: CONFIGURATION_SETTINGS_INVENTORY,
        ...(args.diagnosticCode ? { diagnosis: configurationDiagnostic(args.diagnosticCode) } : {}),
        sameRunCapabilities: framework.options.nativeExecutor ? ["connector.enable"] : [],
        instruction: "Use the registered descriptions/fields for the user's normal task request. Propose only an actual missing requirement or explicit setting change, never defaults already effective. All mode includes every installed Skill. Users enter API keys only in the secure card. P1/model settings affect newly queued runs, never this active run. Unsupported inventory rows must not produce cards or success claims. Network failures, empty results and rate limits do not establish missing credentials or a need to loosen limits." }) }] };
    },
  };
  const proposalParameters = framework.service.registry.proposalParameters();
  const propose: AgentTool<typeof proposalParameters> = {
    name: "propose_configuration", label: "Propose a supported configuration change",
    description: "Prepare a registered configuration choice card. Inspect first for exact fields, current values, scope and availability. Supports model selection/connection/credential repair, ready connectors, installed Skills/Libraries, real thinking controls and session review. Never pass secrets, file paths, commands or approval. A needed connector pauses Native at a durable checkpoint and resumes the SAME task after the user's explicit choice (save/run-only/available alternative/dismiss). Model and P1 changes affect newly queued runs only: do not claim this task was reconfigured. Do not propose already effective defaults or repeat a declined need.",
    parameters: proposalParameters,
    execute: async (_id, args) => {
      try {
        object(args, ["capabilityId", "values", "reason", "scope"], "proposal");
        const id = args.capabilityId as ConfigurationCapabilityId;
        const values = framework.service.registry.agentValues(id, args.values);
        if (args.scope !== undefined && args.scope !== "session" && args.scope !== "project") throw new ConfigurationError("INVALID_CONFIGURATION_SCOPE", "Invalid scope");
        const session = store.assertSessionWritable(sessionId);
        const settings = store.getSessionSettings(sessionId).effective;
        const run = framework.options.nativeExecutor ? await store.getSessionRun(sessionId, runId) : undefined;
        const runtimeEnabledConnectorIds = run ? nativeApprovedRunConnectorIds(store, run) : settings.enabledConnectorIds;
        if (id === "skill.select" && Array.isArray(values.enabledSkillIds)) {
          const installed = store.configurationSnapshot(sessionId, id, id).data.installedSkillIds as string[];
          if (values.enabledSkillIds.some(id => !installed.includes(id))) throw new ConfigurationError("CONFIGURATION_RESOURCE_UNAVAILABLE", "Unknown installed Skill");
        }
        const alreadyEffective = id === "connector.enable" ? runtimeEnabledConnectorIds.includes(String(values.connectorId))
          : id === "model.select" ? settings.modelId === values.modelId
          : id === "skill.select" && values.skillSelectionMode === undefined && values.enabledSkillLibraryIds === undefined && settings.skillSelectionMode === "all";
        if (alreadyEffective) return { content: [{ type: "text", text: JSON.stringify({ status: "already_effective", instruction: "This capability is already effective. Continue the task without requesting configuration." }) }] };
        const request = await framework.propose(sessionId, { protocolVersion: 3, capabilityId: id, values,
          ...(id === "connector.enable" ? { runId } : {}),
          ...(args.scope ? { scope: args.scope === "project" ? { kind: "project" as const, id: session.projectId } : { kind: "session" as const, id: sessionId } } : {}),
          reason: typeof args.reason === "string" ? args.reason : "Requested configuration for the user's task" });
        return { content: [{ type: "text", text: JSON.stringify({ requestId: request.id, capabilityId: request.capabilityId, state: request.state,
          activationMode: request.activation.mode, scope: request.scope,
          instruction: id === "connector.enable" ? "Configuration is pending. Yield at the safe tool-batch boundary; the system resumes this same task after the user's selected configuration, alternative or refusal. Do not produce a final task answer while waiting."
            : "The card is ready. This is a future-run setting: this active task and already queued runs keep their frozen configuration. Never ask for credentials in chat." }) }] };
      } catch (error) {
        // Arbitrary provider/input text must never be copied into a model-facing tool result.
        return { content: [{ type: "text", text: JSON.stringify({ error: error instanceof ConfigurationError ? error.code : "INVALID_CONFIGURATION_INPUT",
          instruction: error instanceof ConfigurationError && error.code === "CONFIGURATION_NEED_ALREADY_DECIDED"
            ? "Do not request this same configuration need again. Respect the recorded choice or refusal and continue the original task with available alternatives or independent work; explain a genuine blocker without repeating a card."
            : "This configuration request is unavailable or invalid. Use only the capability directory and secure UI; do not write configuration files or obtain credentials through chat." }) }] };
      }
    },
  };
  return [inspect, propose];
}
