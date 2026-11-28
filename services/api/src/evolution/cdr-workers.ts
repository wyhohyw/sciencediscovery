// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
import { createHash } from "node:crypto";
import type { ExecutionOwner, PermissionEpoch, RemoteWorkspaceSnapshot } from "@sciencediscovery/schema";

export interface CdrWorkerClient {
  getShellExecution(id: string, owner: ExecutionOwner): Promise<unknown>;
  cancelShellExecution?(id: string, owner: ExecutionOwner): Promise<unknown>;
  snapshotRemoteWorkspace?(workspace: string, paths: string[]): Promise<RemoteWorkspaceSnapshot>;
  streamWorkspaceSnapshot?(snapshot: RemoteWorkspaceSnapshot, path: string): Promise<AsyncIterable<Uint8Array>>;
  uploadWorkspaceSnapshotFile?(workspace: string, path: string, chunks: AsyncIterable<Uint8Array>,
    metadata: { size: number; sha256: string; executable?: number }, conflict: "reject" | "overwrite"): Promise<void>;
  deleteRemoteWorkspace?(workspace: string): Promise<void>;
}

export interface CdrPlacement {
  runnerId: string;
  cards: number[];
  epoch: PermissionEpoch;
  workspaceKey: string;
  workspaceRoot: string;
}

export class CdrWorkerError extends Error {}
const digest = (text: string) => createHash("sha256").update(text).digest("hex");
const fingerprint = (where: CdrPlacement) => JSON.stringify([
  where.runnerId, [...new Set(where.cards)].sort((a, b) => a - b), where.epoch.id, where.workspaceKey, where.workspaceRoot,
]);

/** Private worker roots bypass the Runner's per-Workspace serialization. */
export class CdrWorkers {
  readonly capacity: number;
  private readonly layout: CdrPlacement;
  private readonly executionSlots = new Map<string, string>();
  private readonly confirmed = new Set<string>();
  private readonly failedPublications = new Set<string>();
  private readonly roots = new Set<string>();
  private readonly publications = new Map<string, Promise<void>>();
  private tail: Promise<void> = Promise.resolve();
  private closed = false;

  constructor(private readonly runId: string, where: CdrPlacement, private readonly client: CdrWorkerClient) {
    if (!client.snapshotRemoteWorkspace || !client.streamWorkspaceSnapshot || !client.uploadWorkspaceSnapshotFile || !client.deleteRemoteWorkspace) {
      throw new CdrWorkerError("CDR workers require Runner snapshot and streaming transfer support");
    }
    const cards = [...new Set(where.cards)].sort((a, b) => a - b);
    if (!cards.length || cards.some(card => !Number.isSafeInteger(card) || card < 0)) {
      throw new CdrWorkerError("Invalid CDR worker card selection");
    }
    this.layout = structuredClone({ ...where, cards });
    this.capacity = Math.min(cards.length, 64);
  }

  assertCurrent(where: CdrPlacement): void {
    if (fingerprint(this.layout) !== fingerprint(where)) throw new CdrWorkerError("CDR placement changed; start a new run with the current selection");
  }

  bind(executionId: string, slot: unknown): { cards: number[]; workspaceKey: string } {
    if (this.closed) throw new CdrWorkerError("The CDR run has ended");
    if (typeof slot !== "number" || !Number.isSafeInteger(slot) || slot < 0 || slot >= this.capacity) {
      throw new CdrWorkerError("CDR device slot is outside the authorized card selection");
    }
    const workspaceKey = `evolve-workers/${digest(this.layout.workspaceKey)}/${digest(this.runId)}/slot-${slot}`;
    if (workspaceKey.startsWith(this.layout.workspaceKey + "/") || this.layout.workspaceKey.startsWith(workspaceKey + "/")) {
      throw new CdrWorkerError("CDR worker and primary workspaces must be independent");
    }
    this.executionSlots.set(executionId, workspaceKey);
    this.roots.add(workspaceKey);
    return { cards: [this.layout.cards[slot]!], workspaceKey };
  }

  observe(executionId: string, value: unknown): void {
    if (!this.executionSlots.has(executionId) || !value || typeof value !== "object") return;
    const job = value as { state?: string; evidence?: { processTerminated?: boolean } };
    if ((job.state === "completed" && job.evidence?.processTerminated !== false) ||
        (["failed", "cancelled"].includes(job.state ?? "") && job.evidence?.processTerminated === true)) {
      this.confirmed.add(executionId);
    }
  }

  async publish(executionId: string, structureId: unknown, owner: ExecutionOwner): Promise<void> {
    if (this.closed) throw new CdrWorkerError("The CDR run has ended");
    const source = this.executionSlots.get(executionId);
    if (!source || typeof structureId !== "string" || !/^[a-f0-9]{64}$/.test(structureId)) {
      throw new CdrWorkerError("Invalid CDR worker execution or structure ID");
    }
    const key = `${executionId}:${structureId}`;
    let publication = this.publications.get(key);
    if (!publication) {
      publication = this.tail.then(async () => {
        const job = await this.client.getShellExecution(executionId, owner) as { state?: string; evidence?: { processTerminated?: boolean } };
        if (job.state !== "completed" || job.evidence?.processTerminated === false) throw new CdrWorkerError("CDR artifacts require a completed worker execution");
        this.observe(executionId, job);
        const prefix = `.evolve/cdr-artifacts/${structureId}/`;
        const snapshot = await this.client.snapshotRemoteWorkspace!(source, [prefix.slice(0, -1)]);
        const names = new Set(snapshot.files.map(file => file.path.slice(prefix.length)));
        if (snapshot.workspace !== source || snapshot.files.length !== 4 ||
            snapshot.files.some(file => !file.path.startsWith(prefix) || !["manifest.json", "summary.json", "full.json", "complex.cif", "complex.pdb"].includes(file.path.slice(prefix.length))) ||
            !["manifest.json", "summary.json", "full.json"].every(name => names.has(name)) ||
            !(names.has("complex.cif") || names.has("complex.pdb"))) {
          throw new CdrWorkerError("CDR worker snapshot is incomplete or outside the structure artifact");
        }
        // The primary Workspace serializes publications, not physical folds.
        // Publish the manifest last; confidence bytes are only streamed.
        for (const file of snapshot.files.slice().sort((a, b) => Number(a.path.endsWith("/manifest.json")) - Number(b.path.endsWith("/manifest.json")))) {
          const chunks = await this.client.streamWorkspaceSnapshot!(snapshot, file.path);
          await this.client.uploadWorkspaceSnapshotFile!(this.layout.workspaceKey, file.path, chunks, file, "overwrite");
        }
      });
      this.publications.set(key, publication);
      this.tail = publication.catch(() => { this.failedPublications.add(source); });
    }
    await publication;
  }

  async close(): Promise<void> {
    this.closed = true;
    await this.tail;
    const pending = new Map<string, string[]>();
    for (const [id, root] of this.executionSlots) {
      if (!this.confirmed.has(id)) {
        if (!pending.has(root)) pending.set(root, []);
        pending.get(root)!.push(id);
      }
    }
    const results = await Promise.allSettled([...this.roots].map(async root => {
      const owner = {
        agentId: "evolve", sessionId: this.layout.epoch.sessionId,
      };
      type State = { state?: string; evidence?: { processTerminated?: boolean } };
      const checks = await Promise.allSettled((pending.get(root) ?? []).map(async executionId => {
        let job = await this.client.getShellExecution(executionId, owner) as State;
        if (["queued", "running"].includes(job.state ?? "") && this.client.cancelShellExecution) {
          await this.client.cancelShellExecution(executionId, owner);
          job = await this.client.getShellExecution(executionId, owner) as State;
        }
        this.observe(executionId, job);
        if (!this.confirmed.has(executionId)) throw new CdrWorkerError("Worker termination has not been verified");
      }));
      if (checks.some(check => check.status === "rejected")) {
        throw new CdrWorkerError("Some worker terminations have not been verified");
      }
      if (this.failedPublications.has(root)) throw new CdrWorkerError("Unpublished CDR evidence must be retained");
      await this.client.deleteRemoteWorkspace!(root);
    }));
    if (results.some(result => result.status === "rejected")) throw new CdrWorkerError("Some CDR worker workspaces could not be safely removed; retained for inspection");
  }
}
