// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import type { ManualPlaintextHttpConsent, ModelProfile } from "@sciencediscovery/schema";
import { modelDestination } from "../configuration/model-destination.js";

function normalizedBaseUrl(value: string): string {
  return new URL(value.trim()).href.replace(/\/+$/, "");
}

/** Only used by the authenticated manual settings route, never Agent proposals.
 * No durable grant: the acknowledgement expires with this profile revision. */
export function hasManualHttpConsent(profile: ModelProfile, input: unknown): input is ManualPlaintextHttpConsent {
  if (!input || typeof input !== "object" || Array.isArray(input)) return false;
  const value = input as Partial<ManualPlaintextHttpConsent>;
  if (typeof value.baseUrl !== "string" || value.profileUpdatedAt !== profile.updatedAt) return false;
  try { return normalizedBaseUrl(value.baseUrl) === normalizedBaseUrl(profile.baseUrl); } catch { return false; }
}

export function requiresManualHttpConsent(profile: ModelProfile, hasCredential: boolean): boolean {
  return hasCredential && modelDestination(profile).classification === "public_http";
}
