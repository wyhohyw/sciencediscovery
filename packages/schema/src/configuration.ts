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
}
