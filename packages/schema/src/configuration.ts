// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");

/** The first additional conversational-settings adapter: an installed connector. */
export interface ConnectorConfigurationRequest {
  id: string;
  sessionId: string;
  projectId: string;
  runId: string;
  connectorId: string;
  connectorName: string;
  reason: string;
  baselineConnectorIds: string[];
  state: "awaiting_confirmation" | "applied" | "dismissed" | "stale";
  createdAt: string;
  updatedAt: string;
  /** Set only for a task run; a standalone configuration instruction has no task to resume. */
  continuationRunId?: string;
  /** Present only when a separate Session authorization was active at proposal time. */
  autoApplyAt?: string;
  decisionSource?: "user" | "auto";
  /** Session stop generation at proposal time; a Stop invalidates this request. */
  stopGeneration?: number;
}

export type ConnectorConfigurationPreflightResult =
  | { status: "not_applicable" }
  | { status: "proposal"; request: ConnectorConfigurationRequest }
  | { status: "already_enabled"; connectorName: string }
  | { status: "unavailable"; connectorName: string };
