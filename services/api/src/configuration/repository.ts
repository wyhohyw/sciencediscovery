// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import type { DatabaseSync } from "node:sqlite";
import type { ConfigurationRequestView } from "@sciencediscovery/schema";
import type { ConfigurationApplyReceipt, ConfigurationOutboxRecord, ConfigurationRecord, ConfigurationVerificationAttempt } from "./types.js";
import { canonical, ConfigurationError, digest, string } from "./validation.js";

interface JsonRow { record_json: string; }
interface IdempotencyRow { payload_hash: string; request_id: string; response_json: string | null; }
export class ConfigurationRepository {
  constructor(readonly database: DatabaseSync) {
    database.exec(`
      CREATE TABLE IF NOT EXISTS configuration_requests_v2 (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL, state_version INTEGER NOT NULL, record_json TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS configuration_requests_v2_session ON configuration_requests_v2(session_id);
      CREATE INDEX IF NOT EXISTS configuration_requests_v2_fingerprint ON configuration_requests_v2(session_id, fingerprint, state);
      CREATE TABLE IF NOT EXISTS configuration_revisions_v2 (request_id TEXT NOT NULL, revision INTEGER NOT NULL, content_hash TEXT NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY(request_id, revision));
      CREATE TABLE IF NOT EXISTS configuration_receipts_v2 (id TEXT PRIMARY KEY, request_id TEXT NOT NULL, revision INTEGER NOT NULL, record_json TEXT NOT NULL, UNIQUE(request_id, revision));
      CREATE TABLE IF NOT EXISTS configuration_outbox_v2 (id TEXT PRIMARY KEY, request_id TEXT NOT NULL, revision INTEGER NOT NULL, session_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, record_json TEXT NOT NULL, UNIQUE(request_id, revision, kind));
      CREATE TABLE IF NOT EXISTS configuration_idempotency_v2 (scope TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL, request_id TEXT NOT NULL, response_json TEXT, PRIMARY KEY(scope, key));
      CREATE TABLE IF NOT EXISTS configuration_verification_attempts_v2 (id TEXT PRIMARY KEY, request_id TEXT NOT NULL, session_id TEXT NOT NULL, state TEXT NOT NULL, record_json TEXT NOT NULL);
    `);
  }
  /** Reuses the host transaction; callbacks must remain synchronous. */
  transaction<T>(action: () => T): T {
    if (this.database.isTransaction) return this.sync(action);
    this.database.exec("BEGIN IMMEDIATE");
    try { const result = this.sync(action); this.database.exec("COMMIT"); return result; }
    catch (error) { this.database.exec("ROLLBACK"); throw error; }
  }
  private sync<T>(action: () => T): T {
    const result = action();
    if (result && typeof (result as { then?: unknown }).then === "function") throw new Error("Configuration transactions must be synchronous");
    return result;
  }
  get(id: string, sessionId?: string): ConfigurationRecord | undefined {
    const row = (sessionId ? this.database.prepare("SELECT record_json FROM configuration_requests_v2 WHERE id = ? AND session_id = ?").get(id, sessionId) : this.database.prepare("SELECT record_json FROM configuration_requests_v2 WHERE id = ?").get(id)) as JsonRow | undefined;
    return row ? JSON.parse(row.record_json) as ConfigurationRecord : undefined;
  }
  list(sessionId: string): ConfigurationRecord[] {
    return (this.database.prepare("SELECT record_json FROM configuration_requests_v2 WHERE session_id = ? ORDER BY rowid DESC").all(sessionId) as unknown as JsonRow[]).map((row) => JSON.parse(row.record_json) as ConfigurationRecord);
  }
  findActive(sessionId: string, fingerprint: string): ConfigurationRecord | undefined {
    const row = this.database.prepare("SELECT record_json FROM configuration_requests_v2 WHERE session_id = ? AND fingerprint = ? AND state IN ('collecting_input','awaiting_confirmation','needs_fix','validating','applying') ORDER BY rowid DESC LIMIT 1").get(sessionId, fingerprint) as JsonRow | undefined;
    return row ? JSON.parse(row.record_json) as ConfigurationRecord : undefined;
  }
  save(record: ConfigurationRecord, expectedStateVersion?: number): void {
    const view = record.view;
    // Revision contents are immutable; phase progress updates only the current request.
    const content = { adapterVersion: record.adapterVersion, schemaVersion: record.schemaVersion, input: record.input, target: record.target, plan: record.plan, resourceVersions: record.resourceVersions, stopGeneration: record.stopGeneration, credentialHandle: record.credentialHandle, scope: view.scope, runId: view.runId,
      ...(view.protocolVersion === 3 ? { protocolVersion: 3, options: view.options, selectedOptionId: view.selectedOptionId, needKey: view.needKey } : {}) };
    const hash = digest(content);
    const previousRevision = this.database.prepare("SELECT content_hash FROM configuration_revisions_v2 WHERE request_id = ? AND revision = ?").get(view.id, view.revision) as { content_hash: string } | undefined;
    if (previousRevision && previousRevision.content_hash !== hash) throw new ConfigurationError("STALE_CONFIGURATION_PREVIEW", "A configuration revision cannot be edited", 409);
    if (expectedStateVersion !== undefined) {
      const result = this.database.prepare("UPDATE configuration_requests_v2 SET fingerprint=?,state=?,revision=?,state_version=?,record_json=? WHERE id=? AND session_id=? AND state_version=?").run(record.fingerprint, view.state, view.revision, view.stateVersion, JSON.stringify(record), view.id, view.sessionId, expectedStateVersion);
      if (result.changes !== 1) throw new ConfigurationError("STALE_CONFIGURATION_PREVIEW", "The configuration request changed", 409);
    } else {
      this.database.prepare("INSERT INTO configuration_requests_v2 (id,session_id,fingerprint,state,revision,state_version,record_json) VALUES (?,?,?,?,?,?,?)").run(view.id, view.sessionId, record.fingerprint, view.state, view.revision, view.stateVersion, JSON.stringify(record));
    }
    if (!previousRevision) this.database.prepare("INSERT INTO configuration_revisions_v2 (request_id,revision,content_hash,record_json) VALUES (?,?,?,?)").run(view.id, view.revision, hash, JSON.stringify(record));
  }
  getRevision(id: string, revision: number): ConfigurationRecord | undefined {
    const row = this.database.prepare("SELECT record_json FROM configuration_revisions_v2 WHERE request_id=? AND revision=?").get(id, revision) as JsonRow | undefined;
    return row ? JSON.parse(row.record_json) as ConfigurationRecord : undefined;
  }
  saveReceipt(receipt: ConfigurationApplyReceipt): void {
    const existing = this.getReceipt(receipt.requestId, receipt.revision);
    if (existing) { if (canonical(existing) !== canonical(receipt)) throw new ConfigurationError("RESOURCE_VERSION_CONFLICT", "Configuration revision already has an application receipt", 409); return; }
    this.database.prepare("INSERT INTO configuration_receipts_v2 (id,request_id,revision,record_json) VALUES (?,?,?,?)").run(receipt.id, receipt.requestId, receipt.revision, JSON.stringify(receipt));
  }
  getReceipt(requestId: string, revision?: number): ConfigurationApplyReceipt | undefined {
    const row = (revision === undefined ? this.database.prepare("SELECT record_json FROM configuration_receipts_v2 WHERE request_id=? ORDER BY revision DESC LIMIT 1").get(requestId) : this.database.prepare("SELECT record_json FROM configuration_receipts_v2 WHERE request_id=? AND revision=?").get(requestId, revision)) as JsonRow | undefined;
    return row ? JSON.parse(row.record_json) as ConfigurationApplyReceipt : undefined;
  }
  enqueueOutbox(record: ConfigurationOutboxRecord): void {
    this.database.prepare("INSERT INTO configuration_outbox_v2 (id,request_id,revision,session_id,kind,state,record_json) VALUES (?,?,?,?,?,?,?) ON CONFLICT(request_id,revision,kind) DO NOTHING").run(record.id, record.requestId, record.revision, record.sessionId, record.kind, record.state, JSON.stringify(record));
  }
  getOutbox(id: string): ConfigurationOutboxRecord | undefined {
    const row = this.database.prepare("SELECT record_json FROM configuration_outbox_v2 WHERE id=?").get(id) as JsonRow | undefined;
    return row ? JSON.parse(row.record_json) as ConfigurationOutboxRecord : undefined;
  }
  listOutbox(filter: { state?: ConfigurationOutboxRecord["state"]; kind?: ConfigurationOutboxRecord["kind"]; sessionId?: string } = {}): ConfigurationOutboxRecord[] {
    const rows = this.database.prepare("SELECT record_json FROM configuration_outbox_v2 ORDER BY rowid").all() as unknown as JsonRow[];
    return rows.map((row) => JSON.parse(row.record_json) as ConfigurationOutboxRecord).filter((record) => (!filter.state || record.state === filter.state) && (!filter.kind || record.kind === filter.kind) && (!filter.sessionId || record.sessionId === filter.sessionId));
  }
  updateOutbox(record: ConfigurationOutboxRecord, expectedState?: ConfigurationOutboxRecord["state"]): boolean {
    const result = expectedState ? this.database.prepare("UPDATE configuration_outbox_v2 SET state=?,record_json=? WHERE id=? AND state=?").run(record.state, JSON.stringify(record), record.id, expectedState) : this.database.prepare("UPDATE configuration_outbox_v2 SET state=?,record_json=? WHERE id=?").run(record.state, JSON.stringify(record), record.id);
    return result.changes === 1;
  }
  completeOutbox(id: string, now = new Date().toISOString()): void {
    const record = this.getOutbox(id); if (record) this.updateOutbox({ ...record, state: "done", updatedAt: now });
  }
  getIdempotency(scope: string, key: string | undefined, payload: unknown): { requestId: string; response?: ConfigurationRequestView } | undefined {
    if (!key) return undefined;
    string(key, "Idempotency-Key", 200);
    const row = this.database.prepare("SELECT payload_hash,request_id,response_json FROM configuration_idempotency_v2 WHERE scope=? AND key=?").get(scope, key) as IdempotencyRow | undefined;
    if (!row) return undefined;
    if (row.payload_hash !== digest(payload)) throw new ConfigurationError("IDEMPOTENCY_MISMATCH", "The idempotency key was already used for different input", 409);
    return { requestId: row.request_id, ...(row.response_json ? { response: JSON.parse(row.response_json) as ConfigurationRequestView } : {}) };
  }
  reserveIdempotency(scope: string, key: string | undefined, payload: unknown, requestId: string): void {
    if (!key) return; string(key, "Idempotency-Key", 200);
    this.getIdempotency(scope, key, payload);
    this.database.prepare("INSERT INTO configuration_idempotency_v2 (scope,key,payload_hash,request_id) VALUES (?,?,?,?) ON CONFLICT(scope,key) DO NOTHING").run(scope, key, digest(payload), requestId);
  }
  finishIdempotency(scope: string, key: string | undefined, view: ConfigurationRequestView): void {
    if (key) this.database.prepare("UPDATE configuration_idempotency_v2 SET response_json=? WHERE scope=? AND key=?").run(JSON.stringify(view), scope, key);
  }
  finishPendingIdempotency(requestId: string, view: ConfigurationRequestView): void {
    this.database.prepare("UPDATE configuration_idempotency_v2 SET response_json=? WHERE request_id=? AND response_json IS NULL").run(JSON.stringify(view), requestId);
  }
  saveVerificationAttempt(attempt: ConfigurationVerificationAttempt): void {
    this.database.prepare("INSERT INTO configuration_verification_attempts_v2 (id,request_id,session_id,state,record_json) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,record_json=excluded.record_json").run(attempt.id, attempt.requestId, attempt.sessionId, attempt.state, JSON.stringify(attempt));
  }
  getVerificationAttempt(id: string): ConfigurationVerificationAttempt | undefined {
    const row = this.database.prepare("SELECT record_json FROM configuration_verification_attempts_v2 WHERE id=?").get(id) as JsonRow | undefined;
    return row ? JSON.parse(row.record_json) as ConfigurationVerificationAttempt : undefined;
  }
  listVerificationAttempts(): ConfigurationVerificationAttempt[] {
    return (this.database.prepare("SELECT record_json FROM configuration_verification_attempts_v2 ORDER BY rowid").all() as unknown as JsonRow[]).map((row) => JSON.parse(row.record_json) as ConfigurationVerificationAttempt);
  }
  deleteSession(sessionId: string): void {
    this.transaction(() => {
      this.database.prepare("DELETE FROM configuration_revisions_v2 WHERE request_id IN (SELECT id FROM configuration_requests_v2 WHERE session_id=?)").run(sessionId);
      this.database.prepare("DELETE FROM configuration_receipts_v2 WHERE request_id IN (SELECT id FROM configuration_requests_v2 WHERE session_id=?)").run(sessionId);
      this.database.prepare("DELETE FROM configuration_idempotency_v2 WHERE request_id IN (SELECT id FROM configuration_requests_v2 WHERE session_id=?)").run(sessionId);
      this.database.prepare("DELETE FROM configuration_outbox_v2 WHERE session_id=?").run(sessionId);
      this.database.prepare("DELETE FROM configuration_verification_attempts_v2 WHERE session_id=?").run(sessionId);
      this.database.prepare("DELETE FROM configuration_requests_v2 WHERE session_id=?").run(sessionId);
    });
  }
}
