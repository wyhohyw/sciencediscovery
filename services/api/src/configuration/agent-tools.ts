// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { Type, type Static } from "typebox";
import type { AgentTool } from "@sciencediscovery/tools";
import type { ConfigurationCapabilityId } from "@sciencediscovery/schema";
import type { SessionStore } from "../store.js";
import { configurationFramework } from "./framework.js";
import { ConfigurationError, object, string } from "./validation.js";
import { nativeApprovedRunConnectorIds } from "./native-choices.js";
import { discoverConfiguration } from "./discovery.js";
import { CONFIGURATION_CONTEXT_LIMITS } from "./context-budget.js";

/** Stable transport only; capability IDs and field schemas stay in server-side discovery. */
export const CONFIGURATION_TOOL_PARAMETERS = Type.Object({
  action: Type.Union([Type.Literal("search"), Type.Literal("describe"), Type.Literal("propose")]),
  query: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
  capabilityId: Type.Optional(Type.String({ minLength: 1, maxLength: 100 })),
  targetId: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })),
  cursor: Type.Optional(Type.String({ maxLength: 100 })),
  limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10 })),
  diagnosticCode: Type.Optional(Type.String({ maxLength: 100 })),
  values: Type.Optional(Type.Object({}, { additionalProperties: true })),
  reason: Type.Optional(Type.String({ maxLength: 1000 })),
  scope: Type.Optional(Type.Union([Type.Literal("session"), Type.Literal("project")])),
}, { additionalProperties: false });

/** Main Agent only. User decisions and credentials remain separate channels. */
export function createConfigurationAgentTools(input: { store: SessionStore; sessionId: string; runId: string }): AgentTool[] {
  const { store, sessionId, runId } = input;
  const framework = configurationFramework(store);
  const result = (value: Record<string, unknown>, maximum: number) => {
    const text = JSON.stringify(value);
    if (Buffer.byteLength(text, "utf8") > maximum) throw new ConfigurationError("CONFIGURATION_DISCOVERY_LIMIT", "Configuration result exceeds context budget");
    return { bounded: true, content: [{ type: "text" as const, text }], ...(value.action ? { details: { configurationAction: value.action } } : {}) };
  };
  const propose = async (args: Static<typeof CONFIGURATION_TOOL_PARAMETERS>) => {
    if (args.reason !== undefined) string(args.reason, "reason", 1000);
    const id = string(args.capabilityId, "capabilityId", 100) as ConfigurationCapabilityId;
    const values = framework.service.registry.agentValues(id, args.values);
    if (args.scope !== undefined && args.scope !== "session" && args.scope !== "project") throw new ConfigurationError("INVALID_CONFIGURATION_SCOPE", "Invalid scope");
    const session = store.assertSessionWritable(sessionId);
    const settings = store.getSessionSettings(sessionId).effective;
    const run = framework.options.nativeExecutor ? await store.getSessionRun(sessionId, runId) : undefined;
    const runtimeEnabledConnectorIds = run ? nativeApprovedRunConnectorIds(store, run) : settings.enabledConnectorIds;
    if (id === "skill.select" && Array.isArray(values.enabledSkillIds)) {
      const installed = store.configurationSkillResources().ids;
      if (values.enabledSkillIds.some(id => !installed.includes(id))) throw new ConfigurationError("CONFIGURATION_RESOURCE_UNAVAILABLE", "Unknown installed Skill");
    }
    const alreadyEffective = id === "connector.enable" ? runtimeEnabledConnectorIds.includes(String(values.connectorId))
      : id === "model.select" ? settings.modelId === values.modelId
      : id === "skill.select" && values.skillSelectionMode === undefined && values.enabledSkillLibraryIds === undefined && settings.skillSelectionMode === "all";
    if (alreadyEffective) return { content: [{ type: "text", text: JSON.stringify({ status: "already_effective", instruction: "This capability is already effective. Continue the task without requesting configuration." }) }] };
    const request = await framework.propose(sessionId, { protocolVersion: 3, capabilityId: id, values,
      runId,
      ...(args.scope ? { scope: args.scope === "project" ? { kind: "project" as const, id: session.projectId } : { kind: "session" as const, id: sessionId } } : {}),
      reason: typeof args.reason === "string" ? args.reason : "Requested configuration for the user's task" });
    return { content: [{ type: "text", text: JSON.stringify({ requestId: request.id, capabilityId: request.capabilityId, state: request.state,
      activationMode: request.activation.mode, scope: request.scope,
      instruction: "Configuration is pending. Yield at the safe tool-batch boundary; the system resumes this same task after the user decision. Do not produce a final answer while waiting. Activation timing is independent: next_run settings do not change this task or already queued runs. Never ask for credentials in chat." }) }] };

  };
  const tool: AgentTool<typeof CONFIGURATION_TOOL_PARAMETERS> = { name: "configuration", label: "Find and propose settings",
    description: "When a task needs a setting change, search relevant capabilities/resources; describe one capability for fields/current state; propose a user choice card. If a step fails, also search whether a setting change can supply what is missing and propose a card; that is an additional route, and other tools remain valid. Query first; never guess IDs or send secrets. Proposals do not approve changes. Read activation timing; do not repeat effective or declined changes. Network failures do not justify changing keys or permissions.",
    parameters: CONFIGURATION_TOOL_PARAMETERS,
    execute: async (_id, args) => {
      try {
        if (args.action === "search" || args.action === "describe") {
          object(args, args.action === "search" ? ["action", "query", "diagnosticCode", "cursor", "limit"] : ["action", "capabilityId", "targetId", "query", "cursor", "limit"], "discovery");
          if (args.targetId !== undefined) string(args.targetId, "targetId", 500);
          return result(await discoverConfiguration(store, sessionId, runId, args.action, args), CONFIGURATION_CONTEXT_LIMITS[args.action]);
        }
        object(args, ["action", "capabilityId", "values", "reason", "scope"], "proposal");
        if (args.action !== "propose") throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "Unknown action");
        const output = await propose(args);
        return result({ action: "propose", capabilityId: args.capabilityId, ...JSON.parse(output.content[0]!.text) }, CONFIGURATION_CONTEXT_LIMITS.propose);
      } catch (error) {
        return result({ error: error instanceof ConfigurationError ? error.code : "INVALID_CONFIGURATION_INPUT",
          instruction: error instanceof ConfigurationError && error.code === "CONFIGURATION_DECISION_PENDING"
            ? "An existing configuration decision is pending. Yield at the safe boundary and wait; do not make more proposals or finish the task."
            : error instanceof ConfigurationError && error.code === "CONFIGURATION_NEED_ALREADY_DECIDED"
            ? "Respect the recorded choice or refusal. Continue using available alternatives; do not repeat this configuration need."
            : "Use search/describe for supported targets and fields. Do not write configuration files, bypass permissions or collect credentials in chat." }, CONFIGURATION_CONTEXT_LIMITS.propose);
      }
    },
  };
  return [tool];
}
