// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import type { ApiClient } from "./client.js";
import type { ConfigurationRequestActions } from "@sciencediscovery/configuration-ui";
import type { ConnectorConfigurationRequest, ConfigurationRequestView, ConfigurationCapabilityId,
  ConfigurationValues, ConfigurationScope, ConfigurationPreviewInput, ConfigurationCredentialInput,
  ConfigurationCredentialDraftView, ConfigurationDecisionInput } from "@sciencediscovery/schema";

/** Historical connector records retain their own decision contract. */
export type DesktopConfigurationRequest = ConfigurationRequestView | ConnectorConfigurationRequest;

export function decideLegacyConnectorConfiguration(client: ApiClient, request: ConnectorConfigurationRequest,
  decision: "apply" | "dismiss"): Promise<ConnectorConfigurationRequest> {
  return client.request(configurationRequestPath(request.sessionId, request.id, "decision"), {
    method: "POST", body: JSON.stringify({ decision }),
  });
}

export function listConfigurationRequests(client: ApiClient, sessionId: string, signal?: AbortSignal): Promise<{ items: Array<ConnectorConfigurationRequest | ConfigurationRequestView> }> {
  return client.request(`/api/sessions/${encodeURIComponent(sessionId)}/configuration-requests`, { signal });
}

export function createConfigurationRequest(client: ApiClient, sessionId: string, input: { protocolVersion?: 3; capabilityId: ConfigurationCapabilityId; values: ConfigurationValues; scope?: ConfigurationScope }, idempotencyKey: string): Promise<ConfigurationRequestView> {
  return client.request(`/api/sessions/${encodeURIComponent(sessionId)}/configuration-requests`, {
    method: "POST", body: JSON.stringify(input), headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getConfigurationRequest(client: ApiClient, sessionId: string, requestId: string): Promise<ConfigurationRequestView> {
  return client.request(configurationRequestPath(sessionId, requestId));
}

export function previewConfigurationRequest(client: ApiClient, sessionId: string, requestId: string, input: ConfigurationPreviewInput, idempotencyKey: string): Promise<ConfigurationRequestView> {
  return client.request(configurationRequestPath(sessionId, requestId, "preview"), {
    method: "POST", body: JSON.stringify(input), headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function consentConfigurationEndpoint(client: ApiClient, sessionId: string, requestId: string, input: { revision: number; endpointTarget: string }, idempotencyKey: string): Promise<ConfigurationRequestView> {
  return client.request(configurationRequestPath(sessionId, requestId, "endpoint-consent"), {
    method: "POST", body: JSON.stringify(input), headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function consentConfigurationSecurity(client: ApiClient, sessionId: string, requestId: string,
  input: { revision: number; targetDigest: string; acknowledged: true }, idempotencyKey: string): Promise<ConfigurationRequestView> {
  return client.request(configurationRequestPath(sessionId, requestId, "security-consent"), {
    method: "POST", body: JSON.stringify(input), headers: { "Idempotency-Key": idempotencyKey },
  });
}

/** Credentials use a separate authenticated channel, never message/run input. */
export function submitConfigurationCredential(client: ApiClient, sessionId: string, requestId: string, input: ConfigurationCredentialInput, idempotencyKey: string): Promise<ConfigurationCredentialDraftView> {
  return client.request(configurationRequestPath(sessionId, requestId, "credentials"), {
    method: "POST", body: JSON.stringify(input), headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function decideConfigurationRequest(client: ApiClient, sessionId: string, requestId: string, input: ConfigurationDecisionInput, idempotencyKey: string): Promise<ConfigurationRequestView> {
  return client.request(configurationRequestPath(sessionId, requestId, "decision"), {
    method: "POST", body: JSON.stringify(input), headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function continueConfigurationRequest(client: ApiClient, sessionId: string, requestId: string, idempotencyKey: string): Promise<ConfigurationRequestView> {
  return client.request(configurationRequestPath(sessionId, requestId, "continue"), {
    method: "POST", body: JSON.stringify({}), headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function reverseConfigurationRequest(client: ApiClient, sessionId: string, requestId: string, idempotencyKey: string): Promise<ConfigurationRequestView> {
  return client.request(configurationRequestPath(sessionId, requestId, "reversal"), {
    method: "POST", body: JSON.stringify({}), headers: { "Idempotency-Key": idempotencyKey },
  });
}

function configurationRequestPath(sessionId: string, requestId: string, operation?: string): string {
  return `/api/sessions/${encodeURIComponent(sessionId)}/configuration-requests/${encodeURIComponent(requestId)}${operation ? `/${operation}` : ""}`;
}


/** No credential values or promises are retained by this adapter. */
export function configurationRequestActions(client: ApiClient, request: ConfigurationRequestView): ConfigurationRequestActions {
  const { sessionId, id } = request;
  return {
    preview: (input, key) => previewConfigurationRequest(client, sessionId, id, input, key),
    consent: (input, key) => consentConfigurationEndpoint(client, sessionId, id, input, key),
    securityConsent: (input, key) => consentConfigurationSecurity(client, sessionId, id, input, key),
    credential: (input, key) => submitConfigurationCredential(client, sessionId, id, input, key),
    decide: (input, key) => decideConfigurationRequest(client, sessionId, id, input, key),
    query: () => getConfigurationRequest(client, sessionId, id),
    continue: (key) => continueConfigurationRequest(client, sessionId, id, key),
    reversal: (key) => reverseConfigurationRequest(client, sessionId, id, key),
  };
}
