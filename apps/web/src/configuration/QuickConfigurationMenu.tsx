// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");

import { useRef, useState } from "react";
import type { ConfigurationCapabilityId } from "@sciencediscovery/schema";
import { useLocale } from "../i18n/index.js";
import { configurationMessages } from "./messages.js";
import { ApiRequestError } from "../api/auth.js";

/** Explicit lightweight entry; opening a session never creates a proposal. */
export function QuickConfigurationMenu({ disabled, onRequest, hiddenCapabilities = [] }: {
  disabled: boolean;
  hiddenCapabilities?: ConfigurationCapabilityId[];
  onRequest: (capability: ConfigurationCapabilityId, idempotencyKey: string) => Promise<void>;
}) {
  const { locale } = useLocale();
  const m = configurationMessages(locale);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const disclosure = useRef<HTMLDetailsElement>(null);
  const keys = useRef(new Map<ConfigurationCapabilityId, string>());
  const busyRef = useRef(false);
  async function request(capability: ConfigurationCapabilityId) {
    if (disabled || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(undefined);
    let key = keys.current.get(capability);
    if (!key) { key = crypto.randomUUID(); keys.current.set(capability, key); }
    try {
      await onRequest(capability, key);
      keys.current.delete(capability);
      if (disclosure.current) disclosure.current.open = false;
    } catch (reason) {
      setError(reason instanceof ApiRequestError && reason.code === "THINKING_CONTROL_UNSUPPORTED" ? m.thinkingUnsupported
        : reason instanceof ApiRequestError && reason.code === "CONFIGURATION_BACKEND_UNSUPPORTED" ? m.backendUnsupported : m.requestFailed);
    }
    finally { busyRef.current = false; setBusy(false); }
  }
  return <details className="configuration-quick-menu" ref={disclosure} onKeyDown={(event) => {
    if (event.key !== "Escape" || !disclosure.current) return;
    disclosure.current.open = false;
    disclosure.current.querySelector("summary")?.focus();
  }}>
    <summary aria-disabled={disabled || busy} onClick={(event) => { if (disabled || busy) event.preventDefault(); }}>{m.openMenu}</summary>
    <div className="configuration-quick-options" aria-label={m.openMenu} aria-busy={busy}>
      <p>{m.menuHint}</p>
      {(Object.keys(m.capability) as ConfigurationCapabilityId[]).filter(capability => !hiddenCapabilities.includes(capability)).map((capability) => <button key={capability} type="button" disabled={disabled || busy}
        onClick={() => void request(capability)}>{m.capability[capability]}</button>)}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  </details>;
}
