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

import { request } from "undici";

import { endpointRoot, isAnthropicEndpoint, proxyDispatcher } from "@sciencediscovery/model";
import { DEFAULT_MODEL_API_VARIANT, MODEL_API_VARIANTS } from "@sciencediscovery/schema";
import type {
  ModelConnectivityTestCategory,
  ModelConnectivityTestResult,
  ModelProfile,
  ResolvedProxy,
} from "@sciencediscovery/schema";

const CONNECTIVITY_TIMEOUT_MS = 10_000;
const CONNECTIVITY_MAX_TOKENS = 8;
const MAX_RESPONSE_BYTES = 64 * 1024;

interface ModelConnectivityTestOptions {
  allowMissingToken?: boolean;
  apiToken?: string;
  profile: ModelProfile;
  resolveProxy: () => ResolvedProxy;
  timeoutMs?: number;
}

interface ConnectivityRequest {
  body: string;
  headers: Record<string, string>;
  url: string;
  validate: (payload: unknown) => boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function elapsedSince(startedAt: number): number {
  return Math.max(0, Date.now() - startedAt);
}

function result(
  category: ModelConnectivityTestCategory,
  startedAt: number,
  message: string,
  providerStatus?: number,
): ModelConnectivityTestResult {
  return {
    category,
    latencyMs: elapsedSince(startedAt),
    message,
    ok: category === "ok",
    ...(providerStatus === undefined ? {} : { providerStatus }),
    testedAt: new Date().toISOString(),
  };
}

function classifyProviderStatus(status: number, startedAt: number): ModelConnectivityTestResult {
  if (status === 401 || status === 403) {
    return result("authorization", startedAt, "API key is invalid or is not authorized", status);
  }
  if (status === 404) {
    return result("not_found", startedAt, "Base URL or model ID was not found", status);
  }
  if (status === 429) {
    return result("rate_limited", startedAt, "Provider is rate limiting requests or the account has insufficient quota", status);
  }
  return result("provider_error", startedAt, "The model provider rejected the request or is temporarily unavailable", status);
}

function validOpenAiCompletion(payload: unknown): boolean {
  if (!isRecord(payload) || !Array.isArray(payload.choices) || payload.choices.length === 0) return false;
  const choice = payload.choices[0];
  if (!isRecord(choice) || !isRecord(choice.message)) return false;
  const { content, reasoning_content: reasoningContent, tool_calls: toolCalls } = choice.message;
  return content === null
    || typeof content === "string"
    || typeof reasoningContent === "string"
    || Array.isArray(toolCalls);
}

function validAnthropicMessage(payload: unknown): boolean {
  return isRecord(payload)
    && payload.type === "message"
    && Array.isArray(payload.content);
}

function validOpenAiResponse(payload: unknown): boolean {
  return isRecord(payload) && payload.object === "response" && Array.isArray(payload.output)
    && payload.status !== "failed" && payload.error == null;
}

function connectivityRequest(profile: ModelProfile, apiToken: string): ConnectivityRequest {
  // Share endpoint suffix normalization with the real model client. Only old
  // profiles without an explicit protocol retain the legacy /api/plan fallback.
  const baseUrl = endpointRoot(profile.baseUrl);
  const protocol = profile.apiProtocol ?? (isAnthropicEndpoint(profile.baseUrl) ? "anthropic-messages" : "openai-chat-completions");
  const variant = profile.apiVariant ?? DEFAULT_MODEL_API_VARIANT[protocol];
  if (!Object.hasOwn(MODEL_API_VARIANTS, protocol) || !MODEL_API_VARIANTS[protocol].includes(variant)) throw new Error("Unsupported protocol variant");
  const endpoint = new URL(baseUrl);
  if (!["http:", "https:"].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) throw new Error("Invalid model endpoint");
  if (protocol === "anthropic-messages") {
    return {
      body: JSON.stringify({
        max_tokens: CONNECTIVITY_MAX_TOKENS,
        messages: [{ role: "user", content: "Reply with OK." }],
        model: profile.model,
        stream: false,
        system: "This is a model connectivity test.",
      }),
      headers: {
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        ...(apiToken ? { "x-api-key": apiToken } : {}),
      },
      url: baseUrl.endsWith("/v1") ? `${baseUrl}/messages` : `${baseUrl}/v1/messages`,
      validate: validAnthropicMessage,
    };
  }
  if (protocol === "openai-responses") {
    return {
      body: JSON.stringify({ model: profile.model, instructions: "This is a model connectivity test.",
        input: [{ role: "user", content: [{ type: "input_text", text: "Reply with OK." }] }],
        stream: false, store: false, max_output_tokens: CONNECTIVITY_MAX_TOKENS }),
      headers: { ...(apiToken ? { authorization: `Bearer ${apiToken}` } : {}), "content-type": "application/json" },
      url: `${baseUrl}/responses`, validate: validOpenAiResponse,
    };
  }
  // The same finite chat dialect controls used by the model client, with
  // thinking disabled for this bounded, non-research connectivity input.
  const dialect = variant === "deepseek" ? { thinking: { type: "disabled" } }
    : variant === "qwen" ? { chat_template_kwargs: { enable_thinking: false } }
      : variant === "minimax" ? { reasoning_split: false }
        : variant === "gemini" ? { reasoning_effort: "none" } : {};
  return {
    body: JSON.stringify({
      max_tokens: CONNECTIVITY_MAX_TOKENS,
      messages: [
        { role: "system", content: "This is a model connectivity test." },
        { role: "user", content: "Reply with OK." },
      ],
      model: profile.model,
      stream: false,
      ...dialect,
    }),
    headers: {
      ...(apiToken ? { authorization: `Bearer ${apiToken}` } : {}),
      "content-type": "application/json",
    },
    url: `${baseUrl}/chat/completions`,
    validate: validOpenAiCompletion,
  };
}

function discardResponse(body: { destroy(): unknown; once(event: "error", listener: () => void): unknown }): void {
  // Undici emits UND_ERR_ABORTED asynchronously when an unread body is
  // destroyed. Consume that expected error instead of leaking it to the host.
  body.once("error", () => undefined);
  body.destroy();
}

async function readBoundedResponse(
  body: AsyncIterable<Uint8Array> & { destroy(): unknown; once(event: "error", listener: () => void): unknown },
): Promise<string | undefined> {
  const decoder = new TextDecoder();
  let text = "";
  let bytes = 0;
  for await (const chunk of body) {
    bytes += chunk.byteLength;
    if (bytes > MAX_RESPONSE_BYTES) {
      // Do not drain an unbounded provider stream after exceeding the budget.
      discardResponse(body);
      return undefined;
    }
    text += decoder.decode(chunk, { stream: true });
  }
  return text + decoder.decode();
}

export async function testModelConnectivity(
  options: ModelConnectivityTestOptions,
): Promise<ModelConnectivityTestResult> {
  const startedAt = Date.now();
  const apiToken = options.apiToken?.trim();
  if (!apiToken && !options.allowMissingToken) {
    return result("missing_token", startedAt, "Save an API key before testing");
  }

  let proxy: ResolvedProxy;
  try {
    proxy = options.resolveProxy();
  } catch {
    return result("network", startedAt, "Could not resolve the configured model proxy");
  }

  let probe: ConnectivityRequest;
  try { probe = connectivityRequest(options.profile, apiToken ?? ""); }
  catch { return result("invalid_response", startedAt, "The endpoint protocol, variant, or URL is not supported"); }
  const controller = new AbortController();
  let dispatcher;
  try { dispatcher = proxyDispatcher(proxy); }
  catch { return result("network", startedAt, "Could not resolve the configured model proxy"); }
  const requestedTimeout = options.timeoutMs;
  const timeoutMs = requestedTimeout !== undefined && Number.isFinite(requestedTimeout)
    ? Math.max(1, Math.min(CONNECTIVITY_TIMEOUT_MS, requestedTimeout)) : CONNECTIVITY_TIMEOUT_MS;
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const response = await request(probe.url, {
      body: probe.body,
      bodyTimeout: 0,
      headers: probe.headers,
      headersTimeout: timeoutMs,
      // Undici request() has no redirect/retry interceptor. In particular this
      // paid POST must never be transparently replayed as an idempotent call.
      idempotent: false,
      method: "POST",
      signal: controller.signal,
      ...(dispatcher ? { dispatcher } : {}),
    });
    if (response.statusCode < 200 || response.statusCode >= 300) {
      discardResponse(response.body);
      return classifyProviderStatus(response.statusCode, startedAt);
    }
    const raw = await readBoundedResponse(response.body);
    if (raw === undefined) {
      return result("invalid_response", startedAt, "The endpoint response exceeded the connectivity-test limit", response.statusCode);
    }
    let payload: unknown;
    try {
      payload = JSON.parse(raw) as unknown;
    } catch {
      return result("invalid_response", startedAt, "The endpoint did not return valid JSON", response.statusCode);
    }
    if (!probe.validate(payload)) {
      return result("invalid_response", startedAt, "The endpoint response is not a supported model completion", response.statusCode);
    }
    return result("ok", startedAt, "Connection succeeded", response.statusCode);
  } catch {
    if (timedOut) {
      return result("timeout", startedAt, "Connection to the model provider timed out");
    }
    return result("network", startedAt, "Could not connect to the model provider; check network and proxy settings");
  } finally {
    clearTimeout(timeout);
    await dispatcher?.close().catch(() => undefined);
  }
}

export class ModelConnectivityTestCoordinator {
  private readonly inFlight = new Map<string, Promise<ModelConnectivityTestResult>>();

  run(modelId: string, test: () => Promise<ModelConnectivityTestResult>): Promise<ModelConnectivityTestResult> {
    const existing = this.inFlight.get(modelId);
    if (existing) return existing;
    const pending = test().finally(() => {
      if (this.inFlight.get(modelId) === pending) this.inFlight.delete(modelId);
    });
    this.inFlight.set(modelId, pending);
    return pending;
  }
}
