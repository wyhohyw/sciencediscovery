// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import type { ConfigurationCredentialInput, ConfigurationDecisionInput, ConfigurationPreviewInput,
  ConfigurationRequestView, SessionRun } from "@sciencediscovery/schema";
import type { SessionStore } from "../store.js";
import { isAutoApplicableConnector } from "../store.js";
import { ConfigurationRegistry } from "./registry.js";
import { ConfigurationRepository } from "./repository.js";
import { ConfigurationRequestService } from "./request-service.js";
import { ConfigurationCredentialBridge } from "./credential-bridge.js";
import { createConfigurationDomainAdapters } from "./domain-adapters.js";
import { createP1ConfigurationAdapters } from "./p1-adapters.js";
import { createSystemConfigurationAdapters } from "./system-adapters.js";
import { SYSTEM_CONFIGURATION_CAPABILITIES } from "./agent-metadata.js";
import { ConfigurationActivationCoordinator } from "./activation-coordinator.js";
import type { ConfigurationApplyReceipt, ConfigurationOutboxRecord, ConfigurationProposalInput, ConfigurationRecord } from "./types.js";
import type { ConfigurationActivationEvidence } from "./runtime-bridge.js";
import { randomUUID } from "node:crypto";
import { canonical, ConfigurationError, integer, object, string } from "./validation.js";
import { applyNativeRunOverlay, nativeConnectorChoiceOptions, validateNativeChoice, nativeExecutionDependencies, nativeExecutionDependenciesCurrent } from "./native-choices.js";
import { configurationNeedKey } from "./need-key.js";
import { ModelDestinationAuthorizer } from "./model-destination.js";
import type { ConfigurationConnectorRegistration } from "./connector-directory.js";

export interface ConfigurationFrameworkOptions {
  connectors?(sessionId: string): readonly ConfigurationConnectorRegistration[];
  connector(sessionId: string, connectorId: string): { name: string; ready: boolean } | undefined;
  nativeExecutor: boolean;
  activate?(record: ConfigurationRecord, receipt: ConfigurationApplyReceipt): Promise<ConfigurationActivationEvidence>;
  resume?(sessionId: string, requestId: string): Promise<SessionRun | undefined>;
}
const frameworks = new WeakMap<SessionStore, ConversationalConfigurationFramework>();
export function configurationFramework(store: SessionStore, options?: ConfigurationFrameworkOptions): ConversationalConfigurationFramework {
  let current = frameworks.get(store);
  if (!current) {
    current = new ConversationalConfigurationFramework(store, options ?? { connector: () => undefined, nativeExecutor: false });
    frameworks.set(store, current);
  } else if (options) current.options = options;
  return current;
}

/** API-owned orchestration. Agents never receive the decision or credential methods. */
export class ConversationalConfigurationFramework {
  readonly repository: ConfigurationRepository;
  readonly service: ConfigurationRequestService;
  readonly credentials: ConfigurationCredentialBridge;
  readonly destinations: ModelDestinationAuthorizer;
  private readonly phaseQueues = new Map<string, Promise<unknown>>();
  private readonly decisionQueues = new Map<string, Promise<unknown>>();
  constructor(readonly store: SessionStore, public options: ConfigurationFrameworkOptions) {
    const database = store.getConfigurationDatabase();
    this.repository = new ConfigurationRepository(database);
    this.credentials = new ConfigurationCredentialBridge(database, store.configurationSecretCipher());
    this.destinations = new ModelDestinationAuthorizer(database);
    const registry = new ConfigurationRegistry();
    for (const adapter of [...createConfigurationDomainAdapters({ store, credentials: this.credentials, destinations: this.destinations,
      connector: (sessionId, id) => this.options.connector(sessionId, id) }), ...createSystemConfigurationAdapters(store), ...createP1ConfigurationAdapters(store, () => this.options.nativeExecutor)]) {
      if (adapter.id === "connector.enable") {
        const apply = adapter.apply!;
        adapter.apply = context => {
          const record = this.repository.get(context.requestId, context.sessionId);
          if (record?.view.decisionSource === "auto") this.assertAutoAuthorized(record);
          const result = apply(context);
          // The legacy record is only the Native checkpoint companion, not a second setting.
          store.resolveConnectorConfigurationCompanionSync(context.sessionId, context.requestId, "applied", record?.view.decisionSource ?? "user");
          return result;
        };
      }
      registry.register(adapter);
    }
    this.service = new ConfigurationRequestService({ repository: this.repository, registry,
      host: { runInTransaction: (sessionId, action) => store.runConfigurationTransaction(sessionId, action),
        executionDependencies: record => nativeExecutionDependencies(store, record),
        afterCommit: () => this.flushNotices(),
        projectEndpointAuthorization: record => this.destinations.view(record),
        snapshot: (sessionId, capabilityId, target) => store.configurationSnapshot(sessionId, capabilityId, target),
        choiceOptions: input => input.capabilityId === "connector.enable" && input.runId && this.options.nativeExecutor
          ? nativeConnectorChoiceOptions({ store, sessionId: input.sessionId, runId: input.runId,
            ready: this.options.connector(input.sessionId, String(input.input.connectorId))?.ready === true }) : [
              { id: "persist", kind: "persisted_apply", label: "Apply configuration", description: "Save the reviewed configuration using its declared scope and activation mode.", available: true },
              { id: "dismiss", kind: "dismiss", label: "Not now", description: "Do not change this configuration.", available: true },
            ],
        validateChoice: context => {
          const record = this.repository.get(context.requestId, context.sessionId);
          if (record) validateNativeChoice(store, { ...record, input: context.input, plan: context.plan,
            target: this.service.registry.get(record.view.capabilityId).target(context.input),
            view: { ...record.view, revision: context.revision, selectedOptionId: context.selectedOption?.id } }, context.selectedOption);
        },
        applyRunOverlay: context => {
          const record = this.repository.get(context.requestId, context.sessionId);
          if (!record) throw new ConfigurationError("CONFIGURATION_REQUEST_NOT_FOUND", "Configuration request not found", 404);
          if (!this.options.nativeExecutor || !this.options.connector(context.sessionId, String(context.input.connectorId))?.ready)
            throw new ConfigurationError("CONFIGURATION_OVERLAY_UNAVAILABLE", "This connector is not ready for task-only Native use", 409);
          return applyNativeRunOverlay(store, record, context);
        },
        resolvePreviewCredential: input => {
          try {
            this.credentials.read(input.credentialHandle, { sessionId: input.sessionId, requestId: input.requestId, revision: input.revision, target: input.target, fieldId: "apiToken" });
            return input.credentialHandle;
          } catch (error) {
            if (!(error instanceof Error) || error.message !== "CREDENTIAL_DRAFT_EXPIRED") throw error;
            if (input.explicit) throw new ConfigurationError("CREDENTIAL_DRAFT_EXPIRED", "This isolated credential draft expired. Supply it again using the credential field.", 410);
            this.credentials.clearRequest(input.requestId);
            return undefined;
          }
        },
        onPreviewRevision: change => {
          if (change.oldTarget === change.newTarget) {
            const previous = this.repository.get(change.requestId, change.sessionId);
            if (previous?.plan.endpointAuthorization) this.destinations.rebind(change.requestId, change.sessionId, change.fromRevision, change.toRevision, previous.plan.endpointAuthorization.endpointTarget);
          }
          if (change.credentialHandle) this.credentials.rebind(change.credentialHandle, {
            sessionId: change.sessionId, requestId: change.requestId, revision: change.fromRevision,
            target: change.oldTarget, fieldId: "apiToken",
          }, change.toRevision);
          else if (change.oldTarget !== change.newTarget) this.credentials.clearRequest(change.requestId);
        },
      } });
  }

  async propose(sessionId: string, input: ConfigurationProposalInput, key?: string): Promise<ConfigurationRequestView> {
    const session = this.store.assertSessionWritable(sessionId);
    if (["skill.select", "thinking.configure", "review.configure"].includes(input.capabilityId) && !this.options.nativeExecutor)
      throw new ConfigurationError("CONFIGURATION_BACKEND_UNSUPPORTED", "These scoped settings have only been integrated with the Native executor. JiuwenSwarm host activation is not verified.", 409);
    await this.reconcileLifecycle(sessionId);
    if (Object.keys(input.values).some(name => name.startsWith("restore"))) throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "Restoration fields are reserved for receipt-based reversal", 400);
    const systemCapability = (SYSTEM_CONFIGURATION_CAPABILITIES as readonly string[]).includes(input.capabilityId);
    if (systemCapability && input.scope && input.scope.kind !== "global")
      throw new ConfigurationError("INVALID_CONFIGURATION_SCOPE", "Timeouts, quotas and Idea Tree settings apply to the whole deployment", 400);
    const scope = input.scope ?? (systemCapability || input.capabilityId === "model.repair_credential" ||
      input.capabilityId === "model.connect" && this.store.listModels().length === 0
      ? { kind: "global" as const } : { kind: "session" as const, id: sessionId });
    if (input.runId) {
      const bound = await this.store.getSessionRun(sessionId, input.runId);
      if (input.protocolVersion === 3) {
        if (!bound || ["completed", "cancelled", "failed", "interrupted"].includes(bound.status) || !this.store.notifications.canWake(sessionId))
          throw new ConfigurationError("CONFIGURATION_TASK_UNAVAILABLE", "This configuration need no longer belongs to an active task", 409);
        const adapter = this.service.registry.get(input.capabilityId);
        const normalized = adapter.normalize(input.values);
        const target = adapter.target(normalized);
        const baseline = this.store.configurationSnapshot(sessionId, input.capabilityId, target);
        const needKey = configurationNeedKey({ runId: input.runId, capabilityId: input.capabilityId, target, snapshot: baseline });
        const prior = this.repository.list(sessionId).find(record => record.view.runId === input.runId && record.view.needKey === needKey
          && record.view.state === "dismissed" && ["dismiss", "alternative"].includes(record.view.choiceReceipt?.kind ?? ""));
        if (prior) throw new ConfigurationError("CONFIGURATION_NEED_ALREADY_DECIDED", "This task already received a decision for the same configuration need. Continue using that choice instead of asking again.", 409);
      }
      if (bound?.configurationWait && bound.configurationCheckpoint && this.repository.get(bound.configurationCheckpoint.requestId, sessionId)?.view.state === "expired")
        throw new ConfigurationError("CONFIGURATION_REPLAN_UNAVAILABLE", "The original task remains paused after its proposal expired. Stop it before retrying; rebinding this checkpoint is not available yet.", 409);
      if (!this.options.nativeExecutor || input.capabilityId !== "connector.enable") {
        throw new ConfigurationError("CONFIGURATION_ACTIVATION_UNAVAILABLE", "This executor cannot change this setting and resume the same task. Use the conversation configuration entry for a future run.", 409);
      }
      const connectorId = typeof input.values.connectorId === "string" ? input.values.connectorId : "";
      if (connectorId === "web") throw new ConfigurationError("CONFIGURATION_ACTIVATION_UNAVAILABLE",
        "Same-run configuration currently supports ready MCP tool sources. Configure built-in Web for a future run.", 409);
      const connector = this.options.connector(sessionId, connectorId);
      if (!connector?.ready) throw new ConfigurationError("CONNECTOR_NOT_READY", "The installed connector is not ready for this session", 409);
      const companion = this.store.proposeConnectorConfiguration({ sessionId, runId: input.runId,
        connectorId, connectorName: connector.name, reason: "CONNECTOR_REQUIRED_FOR_TASK",
        ...(input.protocolVersion === 3 ? { configurationProtocolVersion: 3 as const } : {}) });
      try {
        const view = await this.service.propose(sessionId, { ...input, scope, requestId: companion.id }, key);
        if (input.protocolVersion !== 3 && companion.autoApplyAt && view.state === "awaiting_confirmation" && !view.autoApplyAt) {
          await this.update(view.id, row => { row.view.autoApplyAt = companion.autoApplyAt; });
        }
        if (input.protocolVersion === 3) this.store.withdrawConnectorAutoApply(sessionId, companion.id);
        return this.service.get(sessionId, view.id);
      }
      catch (error) {
        if (!this.repository.get(companion.id, sessionId)) await this.store.runConfigurationTransaction(sessionId,
          () => this.store.resolveConnectorConfigurationCompanionSync(sessionId, companion.id, "stale"));
        throw error;
      }
    }
    if (!session.projectId) throw new ConfigurationError("SESSION_NOT_WRITABLE", "Session has no project", 409);
    return this.service.propose(sessionId, { ...input, scope }, key);
  }

  /** Called only by the authenticated user endpoint; not an Agent tool. */
  async confirmEndpoint(sessionId: string, id: string, input: unknown): Promise<ConfigurationRequestView> {
    await this.reconcileLifecycle(sessionId, id);
    await this.store.runConfigurationTransaction(sessionId, () => {
      const record = this.service.getRecord(sessionId, id); const version = record.view.stateVersion;
      if (record.stopGeneration !== this.store.notifications.generation(sessionId)) throw new ConfigurationError("CONTINUATION_STOPPED", "The task was stopped", 409);
      this.destinations.confirm(record, input);
      record.view.stateVersion++; record.view.updatedAt = new Date().toISOString();
      this.repository.save(record, version);
    });
    return this.service.get(sessionId, id);
  }

  async provideCredential(sessionId: string, id: string, input: ConfigurationCredentialInput, key?: string) {
    object(input, ["expectedRevision", "fieldId", "secret"], "credential"); integer(input.expectedRevision, "expectedRevision");
    string(input.fieldId, "fieldId", 100);
    if (typeof input.secret !== "string" || !input.secret.trim() || input.secret.length > 16_384)
      throw new ConfigurationError("INVALID_CONFIGURATION_CREDENTIAL", "Enter a non-empty credential in its registered secure field", 400, input.fieldId);
    await this.reconcileLifecycle(sessionId, id);
    return this.store.runConfigurationTransaction(sessionId, () => {
      const record = this.service.getRecord(sessionId, id);
      if (record.view.expiresAt && Date.parse(record.view.expiresAt) <= Date.now())
        throw new ConfigurationError("CONFIGURATION_EXPIRED", "This configuration proposal expired", 410);
      if (record.view.revision !== input.expectedRevision || !["collecting_input", "awaiting_confirmation", "needs_fix", "failed"].includes(record.view.state))
        throw new ConfigurationError("STALE_CONFIGURATION_PREVIEW", "Refresh the configuration before supplying a credential", 409);
      if (!record.plan.fields.some(field => field.id === input.fieldId && field.kind === "credential"))
        throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "This field is not a registered credential", 400);
      this.destinations.assert(record, true);
      if (record.stopGeneration !== this.store.notifications.generation(sessionId)) throw new ConfigurationError("CONTINUATION_STOPPED", "The task was stopped", 409);
      return this.credentials.provide({ sessionId, requestId: id, revision: input.expectedRevision,
        fieldId: input.fieldId, target: String(record.plan.data?.credentialTarget ?? record.target) }, input.secret, key);
    });
  }

  async preview(sessionId: string, id: string, input: ConfigurationPreviewInput, key?: string): Promise<ConfigurationRequestView> {
    if (input.values && Object.keys(input.values).some(name => name.startsWith("restore"))) throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "Restoration fields cannot be supplied directly", 400);
    await this.reconcileLifecycle(sessionId, id);
    const old = this.service.getRecord(sessionId, id);
    if (old.view.runId && this.service.registry.get(old.view.capabilityId).target(input.values) !== old.target)
      throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "The connector bound to a paused task cannot be changed. Dismiss it and request the other connector separately.", 400);
    return this.service.preview(sessionId, id, input, key);
  }

  private assertAutoAuthorized(record: ConfigurationRecord): void {
    const session = this.store.assertSessionWritable(record.view.sessionId);
    if (record.view.protocolVersion === 3 || record.view.capabilityId !== "connector.enable" || !session.autoApplyLowRiskConfiguration
      || !isAutoApplicableConnector(String(record.input.connectorId)) || !record.view.autoApplyAt
      || Date.parse(record.view.autoApplyAt) > Date.now() || record.stopGeneration !== this.store.notifications.generation(session.id))
      throw new ConfigurationError("CONFIGURATION_AUTO_NOT_AUTHORIZED", "Automatic configuration is not authorized or no longer eligible", 409);
  }

  async decide(sessionId: string, id: string, input: ConfigurationDecisionInput, key?: string, source: "user" | "auto" = "user"): Promise<ConfigurationRequestView> {
    const prior = this.decisionQueues.get(id) ?? Promise.resolve();
    const task = prior.catch(() => undefined).then(() => this.decideSerial(sessionId, id, input, key, source));
    this.decisionQueues.set(id, task);
    try { return await task; }
    finally { if (this.decisionQueues.get(id) === task) this.decisionQueues.delete(id); }
  }

  private async decideSerial(sessionId: string, id: string, input: ConfigurationDecisionInput, key?: string, source: "user" | "auto" = "user"): Promise<ConfigurationRequestView> {
    object(input, ["revision", "previewDigest", "decision", "selectedOptionId"], "decision");
    integer(input.revision, "revision"); string(input.previewDigest, "previewDigest", 100);
    if (input.decision !== "apply" && input.decision !== "dismiss") throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "Invalid configuration decision", 400);
    await this.reconcileLifecycle(sessionId, id);
    if (source === "auto") this.assertAutoAuthorized(this.service.getRecord(sessionId, id));
    const deciding = this.service.getRecord(sessionId, id);
    if (deciding.view.protocolVersion === 3) {
      const option = deciding.view.options?.find(item => item.id === deciding.view.selectedOptionId);
      if (!option || input.selectedOptionId !== option.id) throw new ConfigurationError("CONFIGURATION_OPTION_REQUIRED", "Confirm the selected option from the latest preview", 409);
      if ((input.decision === "dismiss") !== (option.kind === "dismiss")) throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "The decision does not match the selected option", 400);
    } else if (input.selectedOptionId !== undefined) throw new ConfigurationError("INVALID_CONFIGURATION_INPUT", "A v2 proposal does not accept selected options", 400);
    await this.update(id, record => {
      if (record.view.revision !== input.revision || record.view.previewDigest !== input.previewDigest)
        throw new ConfigurationError("STALE_CONFIGURATION_PREVIEW", "Confirm the latest configuration preview", 409);
      if (record.view.state === "awaiting_confirmation") {
        if (source === "auto") this.assertAutoAuthorized(record);
        record.view.decisionSource = source;
      }
    });
    const result = await this.service.decide(sessionId, id, input, key);
    if (result.state === "applied" || result.state === "dismissed") {
      this.credentials.clearRequest(id);
      await this.settle(sessionId, id);
    }
    if (result.state === "expired" || result.state === "cancelled") await this.reconcileLifecycle(sessionId, id);
    return this.service.get(sessionId, id);
  }

  /** Lifecycle facts never resubmit, resume, or terminate a research run. */
  async reconcileLifecycle(sessionId: string, id?: string): Promise<void> {
    await this.service.reconcileLifecycle(sessionId, id);
    const records = id ? [this.service.getRecord(sessionId, id)] : this.repository.list(sessionId);
    for (const record of records) {
      if (!["expired", "cancelled"].includes(record.view.state)) continue;
      this.credentials.clearRequest(record.view.id);
      if (this.store.getConnectorConfigurationRequest(sessionId, record.view.id)?.state === "awaiting_confirmation")
        await this.store.runConfigurationTransaction(sessionId, () => this.store.resolveConnectorConfigurationCompanionSync(sessionId, record.view.id, "stale"));
    }
  }

  private async update(id: string, mutate: (record: ConfigurationRecord) => void, commit?: () => void): Promise<void> {
    const current = this.repository.get(id);
    if (!current) return;
    await this.store.runConfigurationTransaction(current.view.sessionId, () => {
      const record = this.repository.get(id)!;
      const version = record.view.stateVersion;
      mutate(record); record.view.stateVersion++; record.view.updatedAt = new Date().toISOString();
      this.repository.save(record, version);
      commit?.();
    });
    await this.flushNotices();
  }

  async settle(sessionId: string, id: string): Promise<ConfigurationRequestView> {
    const previous = this.phaseQueues.get(id) ?? Promise.resolve();
    const work = previous.catch(() => undefined).then(() => this.settleSerial(sessionId, id));
    this.phaseQueues.set(id, work);
    try { return await work; } finally { if (this.phaseQueues.get(id) === work) this.phaseQueues.delete(id); }
  }

  private async claimPhase(record: ConfigurationRecord, kind: "activate" | "resume"): Promise<ConfigurationOutboxRecord | undefined> {
    return this.store.runConfigurationTransaction(record.view.sessionId, () => {
      const id = `${record.view.id}:${record.view.revision}:${kind}`;
      const job = this.repository.getOutbox(id);
      if (!job || ["done", "suppressed"].includes(job.state)
        || job.state === "claimed" && Number(job.payload.leaseUntil ?? 0) > Date.now()) return undefined;
      const expectedState = job.state;
      job.state = "claimed"; job.updatedAt = new Date().toISOString();
      job.payload = { ...job.payload, leaseToken: randomUUID(), leaseUntil: Date.now() + 60_000 };
      return this.repository.updateOutbox(job, expectedState) ? job : undefined;
    });
  }
  private finishPhase(job: ConfigurationOutboxRecord, state: "done" | "pending" | "suppressed", extra: Record<string, unknown> = {}): void {
    const current = this.repository.getOutbox(job.id);
    if (!current || current.state !== "claimed" || current.payload.leaseToken !== job.payload.leaseToken) throw new Error("Configuration phase lease was lost");
    current.state = state; current.updatedAt = new Date().toISOString();
    current.payload = { ...current.payload, ...extra }; delete current.payload.leaseToken; delete current.payload.leaseUntil;
    if (!this.repository.updateOutbox(current, "claimed")) throw new Error("Configuration phase changed");
  }
  private enqueueResume(record: ConfigurationRecord): void {
    if (!record.view.runId) return;
    const now = new Date().toISOString();
    this.repository.enqueueOutbox({ id: `${record.view.id}:${record.view.revision}:resume`, requestId: record.view.id,
      revision: record.view.revision, sessionId: record.view.sessionId, kind: "resume", state: "pending",
      payload: { runId: record.view.runId, stopGeneration: record.stopGeneration, previewDigest: record.approvedDigest,
        ...(record.view.choiceReceipt ? { selectedOptionId: record.view.choiceReceipt.optionId, choiceKind: record.view.choiceReceipt.kind,
          needKey: record.view.needKey, ...(record.view.choiceReceipt.alternativeId ? { alternativeId: record.view.choiceReceipt.alternativeId } : {}) } : {}) }, createdAt: now, updatedAt: now });
  }

  private async settleSerial(sessionId: string, id: string): Promise<ConfigurationRequestView> {
    const record = this.service.getRecord(sessionId, id);
    if (!["applied", "dismissed"].includes(record.view.state)) return this.service.get(sessionId, id);
    const stopped = record.stopGeneration !== this.store.notifications.generation(sessionId) || !this.store.notifications.canWake(sessionId);
    if (stopped && record.view.runId) {
      await this.update(id, row => { row.view.resumption.state = "suppressed"; row.view.activation.state = row.view.activation.state === "active" ? "active" : "suppressed"; }, () => {
        for (const item of this.repository.listOutbox({ sessionId })) if (item.requestId === id && ["pending", "claimed"].includes(item.state))
          this.repository.updateOutbox({ ...item, state: "suppressed", updatedAt: new Date().toISOString() }, item.state);
      });
      return this.service.get(sessionId, id);
    }
    if (record.view.state === "dismissed") {
      const companion = this.store.getConnectorConfigurationRequest(sessionId, id);
      if (companion?.state === "awaiting_confirmation") await this.store.runConfigurationTransaction(sessionId,
        () => this.store.resolveConnectorConfigurationCompanionSync(sessionId, id, "dismissed"));
      if (record.view.runId) await this.resume(record);
      return this.service.get(sessionId, id);
    }
    const receipt = this.repository.getReceipt(id, record.view.revision);
    if (!receipt) return this.service.get(sessionId, id);
    if (record.view.activation.state === "active") {
      if (record.view.runId && ["pending", "failed"].includes(record.view.resumption.state)) await this.resume(record);
      return this.service.get(sessionId, id);
    }
    if (!["pending", "failed", "not_requested"].includes(record.view.activation.state)) return this.service.get(sessionId, id);
    const job = await this.claimPhase(record, "activate");
    if (!job) return this.service.get(sessionId, id);
    const coordinator = new ConfigurationActivationCoordinator({
      isCurrent: async (source, saved) => {
        if (source.view.runId && !this.store.notifications.canWake(sessionId)) return false;
        const latest = this.repository.get(id, sessionId);
        if (!latest || latest.view.state !== "applied" || latest.approvedRevision !== saved.revision || latest.approvedDigest !== saved.previewDigest) return false;
        const snapshot = this.store.configurationSnapshot(sessionId, source.view.capabilityId, source.target);
        return snapshot.stopGeneration === source.stopGeneration && nativeExecutionDependenciesCurrent(this.store, source, saved);
      },
      activate: async (source, saved) => {
        if (!this.options.activate) throw new Error("No configuration runtime activation adapter is installed");
        return this.options.activate(source, saved);
      },
      saveActivation: async (_requestId, result) => {
        await this.update(id, row => {
          row.view.activation.state = result.state;
          if (result.evidence) row.view.activation.effectiveRevision = result.evidence.effectiveRevision;
          if (result.code && result.state === "failed") row.view.error = { code: result.code, message: "Configuration was saved, but activation could not be verified.", retryable: true };
          else if (result.state === "active") delete row.view.error;
          if (result.state === "suppressed" && row.view.runId) row.view.resumption.state = "suppressed";
        }, () => {
          this.finishPhase(job, result.state === "failed" ? "pending" : result.state === "suppressed" ? "suppressed" : "done",
            result.evidence ? { activationEvidence: result.evidence } : {});
          if (result.state === "active") this.enqueueResume(record);
        });
      },
      enqueueResume: source => this.resume(source),
    });
    try { await coordinator.activate(record, receipt); }
    catch (error) {
      const current = this.repository.getOutbox(job.id);
      if (current?.state === "claimed" && current.payload.leaseToken === job.payload.leaseToken)
        await this.store.runConfigurationTransaction(sessionId, () => this.finishPhase(job, "pending"));
      throw error;
    }
    return this.service.get(sessionId, id);
  }

  private async resume(record: ConfigurationRecord): Promise<void> {
    const { sessionId, id } = record.view;
    if (!record.view.runId || !this.options.resume) return;
    if (record.view.state === "applied" && record.view.activation.state !== "active") {
      record = this.service.getRecord(sessionId, id);
      if (record.view.activation.state !== "active") return;
    }
    const job = await this.claimPhase(record, "resume");
    if (!job) return;
    try {
      if (record.stopGeneration !== this.store.notifications.generation(sessionId) || !this.store.notifications.canWake(sessionId)) {
        await this.update(id, row => { row.view.resumption.state = "suppressed"; }, () => this.finishPhase(job, "suppressed")); return;
      }
      const existing = await this.store.getSessionRun(sessionId, record.view.runId!);
      // Reconcile a crash after durable enqueue, before the local phase acknowledgement.
      const run = existing?.configurationResume?.requestId === id && (["queued", "running", "completed"].includes(existing.status)
        || existing.status === "blocked" && !existing.configurationWait)
        ? existing : await this.options.resume(sessionId, id);
      if (!run || run.id !== record.view.runId) throw new Error("Original run could not be resumed");
      await this.update(id, row => { row.view.resumption.state = run.status === "completed" ? "completed" : run.status === "running" || run.status === "blocked" && !run.configurationWait ? "started" : run.status === "queued" ? "queued" : "failed"; delete row.view.error; }, () => this.finishPhase(job, "done"));
    } catch {
      await this.update(id, row => { row.view.resumption.state = "failed"; row.view.error = { code: "CONFIGURATION_RESUME_FAILED", message: "The configuration decision was saved, but the original task has not resumed.", retryable: true }; }, () => this.finishPhase(job, "pending"));
    }
  }

  async flushNotices(): Promise<void> {
    if (!this.store.appendConfigurationRequestNotice) return;
    for (const item of this.repository.pendingNotices()) {
      if (!this.store.getSession(item.session_id)) { this.repository.acknowledgeNotice(item.request_id, item.state_version); continue; }
      try {
        await this.store.appendConfigurationRequestNotice(item.session_id, item.request_id);
        this.repository.acknowledgeNotice(item.request_id, item.state_version);
      } catch { /* Durable notification remains pending for reconciliation. */ }
    }
  }

  async tick(): Promise<void> {
    this.credentials.sweep();
    await this.flushNotices();
    for (const item of this.repository.pendingOutbox()) {
      const record = this.repository.get(item.requestId, item.sessionId);
      if (!record || !this.store.getSession(item.sessionId)) {
        this.repository.updateOutbox({ ...item, state: "suppressed", updatedAt: new Date().toISOString() }, item.state);
        continue;
      }
      try { await this.settle(item.sessionId, item.requestId); } catch { /* Keep durable receipts for retry. */ }
    }
    for (const pending of this.repository.dueRequests()) {
      const session = this.store.getSession(pending.view.sessionId);
      if (!session) { this.repository.retireWork(pending.view.id); continue; }
      try {
        await this.reconcileLifecycle(session.id, pending.view.id);
        const record = this.repository.get(pending.view.id, session.id)!;
        if (record.view.autoApplyAt && !session.autoApplyLowRiskConfiguration && record.view.state === "awaiting_confirmation") {
          await this.update(record.view.id, row => { delete row.view.autoApplyAt; });
          this.store.withdrawConnectorAutoApply(session.id, record.view.id);
        }
        if (record.view.runId && ["queued", "started"].includes(record.view.resumption.state)) {
          if (record.stopGeneration !== this.store.notifications.generation(session.id)) {
            await this.update(record.view.id, row => { row.view.resumption.state = "suppressed"; });
          } else {
            const run = await this.store.getSessionRun(session.id, record.view.runId);
            const state = run?.status === "completed" ? "completed" : run?.status === "running" ? "started"
              : run && ["cancelled", "failed", "interrupted"].includes(run.status) ? "failed" : undefined;
            if (state && state !== record.view.resumption.state) await this.update(record.view.id, row => { row.view.resumption.state = state; });
          }
        }
      } catch { /* An archived/deleted request cannot block the next bounded batch. */ }
      finally { const current = this.repository.get(pending.view.id); if (current) this.repository.scheduleWork(current, Date.now() + 1000); }
    }
    await this.flushNotices();
  }
}
