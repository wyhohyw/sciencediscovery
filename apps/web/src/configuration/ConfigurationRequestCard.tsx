// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { DEFAULT_MODEL_API_VARIANT, MODEL_API_VARIANTS, type ModelApiProtocol } from "@sciencediscovery/schema";
import type { ConfigurationAction, ConfigurationCredentialDraftView, ConfigurationCredentialInput, ConfigurationDecisionInput,
  ConfigurationFieldView, ConfigurationPreviewInput, ConfigurationRequestView, ConfigurationScalar } from "@sciencediscovery/schema";
import { ApiRequestError } from "../api/auth.js";
import { useLocale } from "../i18n/index.js";
import { ProcessRecord } from "../ProcessRecord.js";
import { configurationMessages } from "./messages.js";

export interface ConfigurationRequestActions {
  preview(input: ConfigurationPreviewInput, key: string): Promise<ConfigurationRequestView>;
  credential(input: ConfigurationCredentialInput, key: string): Promise<ConfigurationCredentialDraftView>;
  decide(input: ConfigurationDecisionInput, key: string): Promise<ConfigurationRequestView>;
  query(): Promise<ConfigurationRequestView>;
  continue(key: string): Promise<ConfigurationRequestView>;
  reversal(key: string): Promise<ConfigurationRequestView>;
}

function fieldValues(request: ConfigurationRequestView): Record<string, ConfigurationScalar> {
  return Object.fromEntries(request.fields.flatMap((field) => field.kind !== "credential" && field.value !== undefined
    ? [[field.id, field.value]] : []));
}

export function isConfigurationRequestView(request: object): request is ConfigurationRequestView {
  return "capabilityId" in request;
}

/** The form is driven exclusively by the server's registered schema. Secrets and
 * credential handles are component-local, never written into timeline snapshots. */
export function ConfigurationRequestCard({ request, actions, runActive = false, readOnly = false, onUpdate, onUnavailable }: {
  request: ConfigurationRequestView;
  actions: ConfigurationRequestActions;
  runActive?: boolean;
  readOnly?: boolean;
  onUpdate: (request: ConfigurationRequestView) => void;
  onUnavailable?: () => void;
}) {
  const { locale } = useLocale();
  const m = configurationMessages(locale);
  const id = useId();
  const [values, setValues] = useState(() => fieldValues(request));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [mutationsBlocked, setMutationsBlocked] = useState(false);
  const [errorField, setErrorField] = useState<string>();
  const [now, setNow] = useState(Date.now());
  const credentialDraft = useRef<(ConfigurationCredentialDraftView & { revision: number }) | undefined>(undefined);
  const submittedPreview = useRef<{ revision: number; digest?: string } | undefined>(undefined);
  const operationKeys = useRef(new Map<string, string>());
  const busyRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; credentialDraft.current = undefined; }; }, []);
  useEffect(() => {
    setValues(fieldValues(request));
    setSecrets({});
    setDirty(false);
    setRefreshRequired(false);
    setMutationsBlocked(false);
    setErrorField(undefined);
    credentialDraft.current = undefined;
    submittedPreview.current = undefined;
    operationKeys.current.clear();
  }, [request.id, request.revision]);
  useEffect(() => {
    if (!request.autoApplyAt || request.state !== "awaiting_confirmation") return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [request.autoApplyAt, request.state]);
  const hasAction = (action: ConfigurationAction) => request.allowedActions.includes(action);
  const terminal = ["applied", "dismissed", "cancelled", "expired"].includes(request.state);
  const editable = hasAction("preview") && !terminal && !busy && !readOnly && !mutationsBlocked
    && !(request.state === "awaiting_confirmation" && request.autoApplyAt);
  const canApply = hasAction("apply") && request.state === "awaiting_confirmation"
    && Boolean(request.previewDigest) && !dirty && !refreshRequired && !runActive && !readOnly && !mutationsBlocked
    && (!submittedPreview.current || (submittedPreview.current.revision === request.revision && submittedPreview.current.digest === request.previewDigest));
  const scheduledAt = !readOnly && !mutationsBlocked && request.state === "awaiting_confirmation" && request.autoApplyAt && Number.isFinite(Date.parse(request.autoApplyAt))
    ? request.autoApplyAt : undefined;
  const remainingSeconds = scheduledAt ? Math.max(0, Math.ceil((Date.parse(scheduledAt) - now) / 1_000)) : undefined;
  const formattedTime = (value: string) => Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString(locale, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : value;

  function operationKey(operation: string): string {
    let key = operationKeys.current.get(operation);
    if (!key) { key = crypto.randomUUID(); operationKeys.current.set(operation, key); }
    return key;
  }
  function edit(field: ConfigurationFieldView, value: ConfigurationScalar) {
    setDirty(true);
    setError(undefined);
    setErrorField(undefined);
    operationKeys.current.clear();
    credentialDraft.current = undefined;
    if (field.kind === "credential") {
      setSecrets((current) => ({ ...current, [field.id]: String(value) }));
    } else setValues((current) => ({ ...current, [field.id]: value,
      ...(request.capabilityId === "model.connect" && field.id === "apiProtocol" && typeof value === "string" && value in MODEL_API_VARIANTS
        ? { apiVariant: DEFAULT_MODEL_API_VARIANT[value as ModelApiProtocol] } : {}) }));
  }
  async function perform(work: () => Promise<ConfigurationRequestView>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(undefined);
    setErrorField(undefined);
    try {
      const next = await work();
      if (mounted.current) onUpdate(next);
    } catch (reason) {
      if (!mounted.current) return;
      // Do not echo raw provider/network errors; these can contain submitted URLs
      // or credentials. Domain errors in the server-projected view are safe.
      if (reason instanceof ApiRequestError && (reason.status === 401 || reason.status === 403)) {
        setMutationsBlocked(true);
        setError(m.accessDenied);
      } else if (reason instanceof ApiRequestError && reason.status === 404) {
        setMutationsBlocked(true);
        setError(m.notFound);
        onUnavailable?.();
      } else if (reason instanceof ApiRequestError && ["SESSION_NOT_WRITABLE", "CONTINUATION_STOPPED"].includes(reason.code ?? "")) {
        setMutationsBlocked(true);
        setError(reason.code === "CONTINUATION_STOPPED" ? m.stopped : m.readOnly);
      } else if (reason instanceof ApiRequestError && reason.code === "IDEMPOTENCY_MISMATCH") {
        setRefreshRequired(true);
        setError(m.idempotencyMismatch);
      } else if (reason instanceof ApiRequestError && ["CONFIGURATION_WAIT_NOT_READY", "CONFIGURATION_RUN_ACTIVE"].includes(reason.code ?? "")) {
        setRefreshRequired(true);
        setError(m.waitBoundary);
      } else if (reason instanceof ApiRequestError && reason.status === 409) {
        setRefreshRequired(true);
        setError(m.conflict);
      } else if (reason instanceof ApiRequestError && reason.status === 410) {
        credentialDraft.current = undefined;
        setSecrets({});
        setRefreshRequired(true);
        setError(m.expired);
      } else if (reason instanceof ApiRequestError && reason.status === 400) {
        setDirty(true);
        setError(m.invalidInput);
        if (typeof reason.details?.field === "string") setErrorField(reason.details.field);
      } else if (reason instanceof ApiRequestError && reason.status === 429) {
        setRefreshRequired(true);
        setError(m.rateLimited);
      } else {
        setRefreshRequired(true);
        setError(m.requestFailed);
      }
    } finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  async function preview(event: FormEvent) {
    event.preventDefault();
    if (!editable || runActive) return;
    const credentialFields = request.fields.filter((field) => field.kind === "credential" && secrets[field.id]);
    if (credentialFields.length > 1) { setError(m.unsupportedCredentials); return; }
    await perform(async () => {
      if (credentialFields[0]) {
        const fieldId = credentialFields[0].id;
        // Bind the isolated draft to the normalized target, including changes to
        // the endpoint/model in this form. This preview has no network effects.
        const target = await actions.preview({ expectedRevision: request.revision, values }, operationKey("target-preview"));
        if (!mounted.current) return target;
        const uploaded = await actions.credential({ expectedRevision: target.revision, fieldId, secret: secrets[fieldId]! }, operationKey(`credential:${target.revision}`));
        if (!mounted.current) return target;
        credentialDraft.current = { ...uploaded, revision: target.revision };
        setSecrets({});
      }
      const draft = credentialDraft.current;
      if (draft && Date.parse(draft.expiresAt) <= Date.now()) { credentialDraft.current = undefined; throw new ApiRequestError("", 410); }
      const next = await actions.preview({ expectedRevision: draft?.revision ?? request.revision, values,
        ...(draft ? { credentialHandle: draft.handle } : {}) }, operationKey("preview"));
      submittedPreview.current = { revision: next.revision, digest: next.previewDigest };
      credentialDraft.current = undefined;
      if (mounted.current) { setDirty(false); setRefreshRequired(false); }
      return next;
    });
  }
  const title = m.capability[request.capabilityId];
  const scope = `${m.scope[request.scope.kind]}${request.scope.kind !== "global" ? ` · ${request.scope.id}` : ""}`;
  const summary = `${title} · ${m.state[request.state]}${request.state === "applied" ? ` · ${m.resumption[request.resumption.state]}` : ""}`;
  const details = <>
    <p>{request.reason.message}</p>
    <dl className="configuration-impact">
      <dt>{m.resource}</dt><dd>{request.impact.resourceLocation}</dd>
      <dt>{m.scopeLabel}</dt><dd>{request.impact.effectiveScope || scope}</dd>
      <dt>{m.activationLabel}</dt><dd>{m.mode[request.activation.mode]}</dd>
    </dl>
    <p>{request.impact.message}</p>
    {request.changes.length ? <div className="configuration-diff" aria-label={m.changes}>
      <strong>{m.changes}</strong>
      <ul>{request.changes.map((change, index) => <li key={`${change.field}:${index}`}><strong>{change.field}</strong>: <span>{change.before ?? m.unset}</span> → <span>{change.after ?? m.unset}</span></li>)}</ul>
    </div> : null}
    <section className="configuration-verification" aria-label={m.verification}>
      <strong>{m.verification}</strong>
      <p>{request.verificationPlan.targets.join(", ") || m.noTargets}</p>
      <p>{request.verificationPlan.actions.join(", ") || m.noActions} · {m.requestBudget}: {request.verificationPlan.maxRequests}</p>
      {request.verificationPlan.mayCharge ? <p className="configuration-charge">{m.mayCharge}</p> : null}
    </section>
    <dl className="configuration-impact configuration-stages" aria-label={m.progress}>
      <dt>{m.saved}</dt><dd>{m.state[request.state]}</dd>
      <dt>{m.activationLabel}</dt><dd>{m.activation[request.activation.state]}</dd>
      <dt>{m.resumptionLabel}</dt><dd>{m.resumption[request.resumption.state]}</dd>
      {request.decisionSource ? <><dt>{m.decisionSourceLabel}</dt><dd>{m.decisionSource[request.decisionSource]}</dd></> : null}
      <dt>{m.updatedAt}</dt><dd><time dateTime={request.updatedAt}>{formattedTime(request.updatedAt)}</time></dd>
    </dl>
    {request.error ? <p className="conversation-configuration-error" role="alert">{request.error.message} <span>({request.error.code})</span></p> : null}
    {request.state === "stale" ? <p role="status">{m.stale}</p> : null}
    {request.state === "needs_fix" ? <p role="status">{m.needsFix}</p> : null}
  </>;
  const secondaryActions = <>
    {hasAction("query") || refreshRequired || error ? <button className="secondary-button" disabled={busy} type="button" onClick={() => void perform(async () => {
      const next = await actions.query();
      if (mounted.current) { setValues(fieldValues(next)); setSecrets({}); setDirty(false); setRefreshRequired(false); }
      credentialDraft.current = undefined;
      submittedPreview.current = { revision: next.revision, digest: next.previewDigest };
      operationKeys.current.clear();
      return next;
    })}>{m.refresh}</button> : null}
    {hasAction("continue") ? <button className="secondary-button" disabled={busy || runActive || readOnly || mutationsBlocked} type="button" onClick={() => void perform(() => actions.continue(operationKey("continue")))}>{m.retryContinuation}</button> : null}
    {hasAction("reversal") ? <button className="secondary-button" disabled={busy || readOnly || mutationsBlocked} type="button" onClick={() => void perform(() => actions.reversal(operationKey("reversal")))}>{m.reversal}</button> : null}
  </>;
  if (terminal) return <ProcessRecord className="conversation-configuration-record" label={summary} failed={request.activation.state === "failed" || request.resumption.state === "failed"}>
    <section aria-label={title} className="conversation-configuration-record-details">
      {details}<div className="conversation-configuration-actions">{secondaryActions}</div>
      {error ? <p role="alert" className="conversation-configuration-error">{error}</p> : null}
    </section>
  </ProcessRecord>;
  return <section id={`configuration-request-${request.id}`} aria-label={title} className="conversation-configuration-card configuration-request" aria-busy={busy}>
    <div className="conversation-configuration-heading">
      <div><span className="eyebrow">{m.eyebrow}</span><h3>{title}</h3></div>
      <span className="conversation-configuration-status" role="status">{m.state[request.state]}</span>
    </div>
    {details}
    {scheduledAt && remainingSeconds !== undefined ? <div className="configuration-auto-apply" role="status">
      <p>{remainingSeconds > 0 ? m.autoCountdown(remainingSeconds) : m.autoDue}</p>
      <p>{m.scheduledAt}: <time dateTime={scheduledAt}>{formattedTime(scheduledAt)}</time></p>
    </div> : null}
    <form className="configuration-fields" onSubmit={(event) => void preview(event)}>
      {request.fields.map((field) => {
        const inputId = `${id}-${field.id}`;
        const descriptionId = `${inputId}-help`;
        const common = { id: inputId, name: field.id, disabled: !editable, required: field.required,
          "aria-describedby": field.description || field.kind === "credential" ? descriptionId : undefined,
          "aria-invalid": request.error?.field === field.id || errorField === field.id || undefined };
        return <div className="configuration-field" key={field.id}>
          <label htmlFor={inputId}>{locale.startsWith("zh") ? m.field[field.id] ?? field.label : field.label}{field.required ? " *" : ""}</label>
          {field.kind === "credential" ? <>
            <input {...common} autoComplete="new-password" type="password" value={secrets[field.id] ?? ""}
              required={false} aria-required={field.required && field.status !== "provided"}
              placeholder={field.status === "provided" ? m.providedPlaceholder : field.placeholder}
              onChange={(event) => edit(field, event.target.value)} />
            <p id={descriptionId}>{m.credential[field.status]} · {m.credentialNote}{field.description ? ` ${field.description}` : ""}</p>
          </> : field.kind === "enum" ? <select {...common} value={String(values[field.id] ?? "")} onChange={(event) => edit(field, event.target.value)}>
            <option value="">{m.choose}</option>
            {(request.capabilityId === "model.connect" && field.id === "apiVariant" && typeof values.apiProtocol === "string" && values.apiProtocol in MODEL_API_VARIANTS
              ? MODEL_API_VARIANTS[values.apiProtocol as ModelApiProtocol].map((variant) => ({ id: variant, label: variant })) : field.choices)
              ?.map((choice) => <option key={choice.id} value={choice.id}>{choice.label}</option>)}
          </select> : field.kind === "boolean" ? <input {...common} required={false} type="checkbox" checked={values[field.id] === true} onChange={(event) => edit(field, event.target.checked)} />
            : <input {...common} type={field.kind === "number" ? "number" : field.kind === "url" ? "url" : "text"}
              value={values[field.id] === undefined ? "" : String(values[field.id])} placeholder={field.placeholder}
              onChange={(event) => edit(field, field.kind === "number" && event.target.value !== "" ? Number(event.target.value) : event.target.value)} />}
          {field.kind !== "credential" && field.description ? <p id={descriptionId}>{field.description}</p> : null}
          {field.help && /^https?:\/\//i.test(field.help.url) ? <a href={field.help.url} target="_blank" rel="noreferrer noopener">{field.help.label}</a> : null}
        </div>;
      })}
      {dirty ? <p role="status">{m.previewRequired}</p> : null}
      <div className="conversation-configuration-actions">
        {hasAction("preview") && !scheduledAt ? <button type="submit" className="secondary-button" disabled={busy || runActive || refreshRequired || readOnly || mutationsBlocked}>{m.preview}</button> : null}
        {hasAction("apply") ? <button type="button" className="primary-button" disabled={busy || !canApply} onClick={() => { if (canApply) void perform(() => actions.decide({ revision: request.revision, previewDigest: request.previewDigest!, decision: "apply" }, operationKey("apply"))); }}>{m.confirm}</button> : null}
        {hasAction("dismiss") ? <button type="button" className="secondary-button" disabled={busy || readOnly || mutationsBlocked} onClick={() => void perform(() => actions.decide({ revision: request.revision, previewDigest: request.previewDigest ?? "", decision: "dismiss" }, operationKey("dismiss")))}>{scheduledAt ? m.cancelChange : m.dismiss}</button> : null}
        {secondaryActions}
      </div>
    </form>
    {runActive ? <p role="status">{m.waitBoundary}</p> : null}
    {error ? <p role="alert" className="conversation-configuration-error">{error}</p> : null}
  </section>;
}
