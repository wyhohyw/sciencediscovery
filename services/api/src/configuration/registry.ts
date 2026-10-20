// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import type { ConfigurationCapabilityId, ConfigurationCapabilityView } from "@sciencediscovery/schema";
import type { ConfigurationAdapter } from "./types.js";
import { ConfigurationError, integer, invalid } from "./validation.js";
const supported = new Set<ConfigurationCapabilityId>(["model.select", "model.connect", "model.repair_credential", "connector.enable"]);
export class ConfigurationRegistry {
  private readonly adapters = new Map<ConfigurationCapabilityId, ConfigurationAdapter>();
  register(adapter: ConfigurationAdapter): this {
    if (!supported.has(adapter.id) || this.adapters.has(adapter.id)) invalid("Unknown or duplicate configuration capability");
    integer(adapter.version, "adapterVersion"); integer(adapter.schemaVersion, "schemaVersion");
    if (!adapter.scopes.length || adapter.scopes.some((scope) => !["session", "project", "global"].includes(scope))) invalid("Invalid adapter scope");
    if (adapter.persistence !== "local_transaction" && adapter.persistence !== "external_control") invalid("Invalid adapter persistence");
    if (adapter.persistence === "local_transaction" && !adapter.apply) invalid("Local adapters require a synchronous domain apply");
    this.adapters.set(adapter.id, adapter); return this;
  }
  get(id: string): ConfigurationAdapter {
    const adapter = this.adapters.get(id as ConfigurationCapabilityId);
    if (!adapter) throw new ConfigurationError("CONFIGURATION_CAPABILITY_UNAVAILABLE", "This configuration capability is not available", 409);
    return adapter;
  }
  list(): ConfigurationCapabilityView[] {
    return [...this.adapters.values()].map((adapter) => ({ id: adapter.id, adapterVersion: adapter.version, schemaVersion: adapter.schemaVersion, scopes: [...adapter.scopes], persistence: adapter.persistence, autoApplyEligible: adapter.autoApplyEligible === true }));
  }
}
