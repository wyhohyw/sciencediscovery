// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { Type } from "typebox";
import type { AgentTool } from "@sciencediscovery/tools";
import type { ConfigurationCapabilityId, ConfigurationValues } from "@sciencediscovery/schema";
import type { SessionStore } from "../store.js";
import { configurationFramework } from "./framework.js";
import { ConfigurationError, object } from "./validation.js";

/** Main Agent only. Deliberately contains no decision, credential, file or command tool. */
export function createConfigurationAgentTools(input: { store: SessionStore; sessionId: string; runId: string }): AgentTool[] {
  const { store, sessionId, runId } = input;
  const framework = configurationFramework(store);
  const inspectParameters = Type.Object({}, { additionalProperties: false });
  const inspect: AgentTool<typeof inspectParameters> = {
    name: "inspect_configuration", label: "Inspect supported settings",
    description: "Read the finite supported configuration capabilities and non-sensitive current session state. This does not probe endpoints or modify settings. No credentials are returned.",
    parameters: inspectParameters,
    execute: async (_id, args) => {
      object(args, [], "inspection");
      const session = store.assertSessionWritable(sessionId);
      const settings = store.getSessionSettings(sessionId).effective;
      return { content: [{ type: "text", text: JSON.stringify({ sessionId: session.id,
        capabilities: framework.service.registry.list(), modelId: settings.modelId ?? null,
        models: store.listModels().map(model => ({ id: model.id, name: model.name, credential: model.hasApiToken ? "provided" : store.modelAllowsMissingToken(model) ? "not_required" : "missing" })),
        enabledConnectorIds: settings.enabledConnectorIds,
        sameRunCapabilities: framework.options.nativeExecutor ? ["connector.enable"] : [],
        instruction: "Propose only a registered capability. Users enter API keys in the secure card, never in this tool or chat. Model changes affect a later run; do not claim to change the running model." }) }] };
    },
  };
  const proposalParameters = Type.Object({
    capabilityId: Type.Union([Type.Literal("model.select"), Type.Literal("model.connect"), Type.Literal("model.repair_credential"), Type.Literal("connector.enable")]),
    values: Type.Object({
      connectorId: Type.Optional(Type.String({ minLength: 1, maxLength: 100 })),
      modelId: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
      name: Type.Optional(Type.String({ maxLength: 200 })), baseUrl: Type.Optional(Type.String({ maxLength: 2048 })),
      model: Type.Optional(Type.String({ maxLength: 512 })),
      apiProtocol: Type.Optional(Type.Union([Type.Literal("openai-chat-completions"), Type.Literal("openai-responses"), Type.Literal("anthropic-messages")])),
      apiVariant: Type.Optional(Type.String({ maxLength: 80 })), tokenOptional: Type.Optional(Type.Boolean()),
    }, { additionalProperties: false }),
  }, { additionalProperties: false });
  const propose: AgentTool<typeof proposalParameters> = {
    name: "propose_configuration", label: "Propose a supported configuration change",
    description: "Prepare a concrete, server-validated configuration card for user confirmation. Inspect first. You can select or connect a model, request credential repair (the user enters the secret in a secure card), or enable an installed ready connector. Never supply API keys, headers, env, file paths, HTML, commands or approval. For a needed connector, call before dependent work; the Native runtime pauses at a committed checkpoint and resumes the SAME task after the user's decision. Model changes apply before a future run and cannot hot-swap the current model. Existing usable defaults need no proposal.",
    parameters: proposalParameters,
    execute: async (_id, args) => {
      try {
        object(args, ["capabilityId", "values"], "proposal");
        object(args.values, ["connectorId", "modelId", "name", "baseUrl", "model", "apiProtocol", "apiVariant", "tokenOptional"], "values");
        const id = args.capabilityId as ConfigurationCapabilityId;
        const request = await framework.propose(sessionId, { capabilityId: id, values: args.values as ConfigurationValues,
          ...(id === "connector.enable" ? { runId } : {}), reason: "Requested configuration for the user's task" });
        return { content: [{ type: "text", text: JSON.stringify({ requestId: request.id, capabilityId: request.capabilityId, state: request.state,
          activationMode: request.activation.mode, scope: request.scope,
          instruction: id === "connector.enable" ? "Configuration is pending. Yield at the safe tool-batch boundary; the system will resume this same task after confirmation or refusal. Do not produce a final task answer while waiting."
            : "The card is ready for the user. This is a future-run setting; the current task's model is unchanged. Never ask the user to paste credentials in chat." }) }] };
      } catch (error) {
        // Arbitrary provider/input text must never be copied into a model-facing tool result.
        return { content: [{ type: "text", text: JSON.stringify({ error: error instanceof ConfigurationError ? error.code : "INVALID_CONFIGURATION_INPUT",
          instruction: "This configuration request is unavailable or invalid. Use only the capability directory and the secure UI; do not write configuration files or obtain credentials through chat." }) }] };
      }
    },
  };
  return [inspect, propose];
}
