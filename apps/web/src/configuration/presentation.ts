// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");

import type { ChatMessage, ConnectorConfigurationRequest, SessionRun } from "@sciencediscovery/schema";

/** Keep a resolved request visible only until its in-conversation receipt exists. */
export function connectorConfigurationPresentation(
  requests: readonly ConnectorConfigurationRequest[],
  runs: readonly SessionRun[],
  messages: readonly ChatMessage[],
  sessionId: string | undefined,
) {
  const continuationByNoticeId = new Map(runs
    .filter((run) => run.userMessageId && run.configurationContinuation)
    .map((run) => [run.userMessageId!, run.configurationContinuation!]));
  const acknowledgedRequestIds = new Set(messages
    .filter((message) => message.kind === "configuration_notice")
    .map((message) => continuationByNoticeId.get(message.id)?.requestId)
    .filter((requestId): requestId is string => Boolean(requestId)));
  const visibleRequests = requests.filter((request) => request.sessionId === sessionId
    && (request.state === "awaiting_confirmation"
      || ((request.state === "stale" || request.state === "applied")
        && !runs.some((run) => run.createdAt > request.updatedAt)
        && !acknowledgedRequestIds.has(request.id))));
  return { continuationByNoticeId, visibleRequests };
}
