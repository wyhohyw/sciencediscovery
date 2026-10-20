// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { Type, type TSchema } from "typebox";
import type { ConfigurationCapabilityId } from "@sciencediscovery/schema";
export interface ConfigurationAgentDescriptor {
  label: string;
  whenNeeded: string;
  fields: Record<string, { kind: "string" | "boolean" | "ids"; maxLength?: number; choices?: readonly string[] }>;
  activation: "next_run" | "same_run_if_bound";
  diagnostics: readonly string[];
}
const id = { kind: "string", maxLength: 200 } as const;
const ids = { kind: "ids" } as const;
const boolean = { kind: "boolean" } as const;
/** Trusted finite descriptions. Each domain adds its fields here once; tools are generated from them. */
export const CONFIGURATION_AGENT_DESCRIPTORS: Record<ConfigurationCapabilityId, ConfigurationAgentDescriptor> = {
  "model.select": { label: "Choose an existing model", whenNeeded: "An explicit model choice or no usable task model. Do not replace a usable inherited default without a requirement.", fields: { modelId: id }, activation: "next_run", diagnostics: ["MODEL_NOT_SELECTED"] },
  "model.connect": { label: "Connect a model service", whenNeeded: "A new model service is needed. The user supplies credentials only in the secure card.", fields: { name: { ...id, maxLength: 200 }, baseUrl: { kind: "string", maxLength: 2048 }, model: { ...id, maxLength: 512 }, apiProtocol: { kind: "string", choices: ["openai-chat-completions", "openai-responses", "anthropic-messages"] }, apiVariant: { ...id, maxLength: 80 }, tokenOptional: boolean }, activation: "next_run", diagnostics: [] },
  "model.repair_credential": { label: "Repair a model credential", whenNeeded: "A structured authentication diagnosis identifies this exact model. A network failure, timeout or rate limit is not authentication evidence.", fields: { modelId: id }, activation: "next_run", diagnostics: ["MODEL_CREDENTIAL_MISSING", "MODEL_AUTHENTICATION_REJECTED"] },
  "connector.enable": { label: "Enable a ready connector", whenNeeded: "The current task requires an installed, ready connector missing from the effective tool set. Respect refusals and alternatives.", fields: { connectorId: { ...id, maxLength: 100 } }, activation: "same_run_if_bound", diagnostics: ["CONNECTOR_DISABLED"] },
  "skill.select": { label: "Select installed Skills and Libraries", whenNeeded: "The user requests a selection of installed Skills or Libraries. In all mode every installed Skill is already available; do not ask to enable it again.", fields: { skillSelectionMode: { kind: "string", choices: ["all", "selected"] }, enabledSkillIds: ids, enabledSkillLibraryIds: ids }, activation: "next_run", diagnostics: ["SKILL_EXCLUDED"] },
  "thinking.configure": { label: "Configure model thinking", whenNeeded: "The user explicitly requests a thinking mode or effort supported by the current model API variant. Applies to future runs, never the active frozen model.", fields: { thinkingMode: { kind: "string", choices: ["auto", "disabled", "enabled"] }, thinkingEffort: { kind: "string", choices: ["low", "medium", "high", "xhigh", "max"] } }, activation: "next_run", diagnostics: ["THINKING_REQUIREMENT"] },
  "review.configure": { label: "Configure session review", whenNeeded: "The user requests review model, semantic review, automatic review or review level for this session. The global Reviewer Specialist switch is separate and cannot be changed here.", fields: { reviewModelId: id, semanticReviewEnabled: boolean, automaticReviewEnabled: boolean, level: { kind: "string", choices: ["quick", "deep"] } }, activation: "next_run", diagnostics: ["REVIEW_REQUIREMENT"] },
};
export function descriptorValuesSchema(descriptor: ConfigurationAgentDescriptor): TSchema {
  return Type.Object(Object.fromEntries(Object.entries(descriptor.fields).map(([name, field]) => {
    const schema = field.kind === "ids" ? Type.Array(Type.String({ minLength: 1, maxLength: 500 }), { maxItems: 500, uniqueItems: true })
      : field.kind === "boolean" ? Type.Boolean()
      : field.choices ? Type.Union(field.choices.map(value => Type.Literal(value))) : Type.String({ maxLength: field.maxLength ?? 200 });
    return [name, Type.Optional(schema)];
  })), { additionalProperties: false });
}
