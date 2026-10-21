// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { useLocale } from "./i18n/index.js";

/** URL parsing matches the server, without importing its model/request runtime. */
export function normalizedModelBaseUrl(baseUrl: string): string {
  try { return new URL(baseUrl.trim()).href.replace(/\/+$/, ""); } catch { return baseUrl.trim(); }
}

export function isPlaintextModelUrl(baseUrl: string): boolean {
  try { return new URL(baseUrl.trim()).protocol === "http:"; } catch { return false; }
}

export function ManualHttpConsent({ baseUrl, checked, disabled, onChange }: {
  baseUrl: string; checked: boolean; disabled: boolean; onChange: (checked: boolean) => void;
}) {
  const { t } = useLocale();
  return <div className="manual-http-consent" role="group" aria-label={t("settings.modelTest.httpTitle")}>
    <strong>{t("settings.modelTest.httpTitle")}</strong>
    <code>{normalizedModelBaseUrl(baseUrl)}</code>
    <span>{t("settings.modelTest.httpRisk")}</span>
    <label><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />{t("settings.modelTest.httpConsent")}</label>
  </div>;
}
