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

import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";

import type { RemoteComputeClient } from "@sciencediscovery/executor";
import {
  effectiveRunnerIds,
  type ScientificArtifactKind,
  type ScientificArtifactVersion,
} from "@sciencediscovery/schema";

import { shortErrorMessage } from "@sciencediscovery/operational-logging";

import { apiLog } from "../logging.js";
import { syncRemoteWorkspace } from "../remote-runner.js";
import type { SessionStore } from "../store.js";

const MAX_DESIGN_BYTES = 80_000_000;

export interface DesignContent {
  hash: string;
  size: number;
}

export interface RegisteredDesign {
  mediaType: string;
  sessionId: string;
  version: ScientificArtifactVersion;
}

/** Same 12-hex prefix the NPU evaluator uses for `antigen-runs/<digest>`. */
export function designDigest(code: string): string | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(code);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
  return createHash("sha256").update(canonicalJson(parsed)).digest("hex").slice(0, 12);
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortValue);
  if (value && typeof value === "object") {
    const source = value as Record<string, unknown>;
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(source).sort()) sorted[key] = sortValue(source[key]);
    return sorted;
  }
  return value;
}

function listedPaths(index: unknown, digest: string): string[] {
  if (!index || typeof index !== "object") return [];
  const record = index as Record<string, unknown>;
  const paths: string[] = [];
  for (const key of ["sequence", "structure"] as const) {
    const path = record[key];
    if (typeof path !== "string") continue;
    const prefix = `antigen_epitope/results/${digest}/`;
    if (!path.startsWith(prefix) || path.includes("..") || path.includes("\\")) continue;
    const name = path.slice(prefix.length);
    if (name !== "sequence.fasta" && name !== "structure.cif" && name !== "structure.pdb") continue;
    paths.push(path);
  }
  return paths;
}

function kindOf(path: string): { kind: ScientificArtifactKind; mediaType: string; title: string } {
  if (path.endsWith(".cif")) return { kind: "structure", mediaType: "chemical/x-mmcif", title: "设计结构" };
  if (path.endsWith(".pdb")) return { kind: "structure", mediaType: "chemical/x-pdb", title: "设计结构" };
  return { kind: "other", mediaType: "text/plain; charset=utf-8", title: "设计序列" };
}

/**
 * Pull the measured design into the session workspace and register it.
 *
 * The pipeline writes on the runner. Until these two files are artifacts, the
 * session panel only has the JSON program and the structure is invisible.
 */
export async function publishMeasuredDesign(options: {
  code: string;
  notify: (input: RegisteredDesign) => void;
  put: (bytes: Buffer) => Promise<DesignContent>;
  remoteCompute: RemoteComputeClient;
  runId: string;
  sessionId: string;
  store: SessionStore;
}): Promise<void> {
  const digest = designDigest(options.code);
  if (!digest) return;
  const session = options.store.getSession(options.sessionId);
  if (!session) return;
  const project = options.store.getProject(session.projectId);
  if (!project) return;
  const hostId = effectiveRunnerIds(project, session).find((id) => id !== "local");
  if (hostId) {
    const indexPath = `antigen_epitope/results/${digest}/index.json`;
    try {
      await syncRemoteWorkspace({
        hostId,
        input: { conflict: "overwrite", direction: "pull", paths: [indexPath] },
        runnerClient: options.remoteCompute.runnerClient(hostId),
        sessionId: options.sessionId,
        store: options.store,
      });
      const index = JSON.parse(await readFile(join(options.store.workspacePath(options.sessionId), indexPath), "utf8")) as unknown;
      const paths = listedPaths(index, digest);
      if (paths.length) {
        await syncRemoteWorkspace({
          hostId,
          input: { conflict: "overwrite", direction: "pull", paths },
          runnerClient: options.remoteCompute.runnerClient(hostId),
          sessionId: options.sessionId,
          store: options.store,
        });
      }
    } catch (error) {
      apiLog.warn("design_artifact_pull_failed", {
        errorMessage: shortErrorMessage(error),
        searchId: options.runId,
      });
    }
  }
  await registerStagedDesigns({
    notify: options.notify,
    put: options.put,
    sessionId: options.sessionId,
    store: options.store,
  });
}

/** Register design files that are already in a session workspace. */
export async function registerStagedDesigns(options: {
  notify: (input: RegisteredDesign) => void;
  put: (bytes: Buffer) => Promise<DesignContent>;
  sessionId?: string;
  store: SessionStore;
}): Promise<void> {
  const sessions = options.sessionId
    ? [options.store.getSession(options.sessionId)].filter((session) => session !== undefined)
    : options.store.listProjects().flatMap((project) => options.store.listSessions(project.id, "all"));
  for (const session of sessions) {
    const root = options.store.workspacePath(session.id);
    let entries: string[] = [];
    try {
      entries = await readdir(join(root, "antigen_epitope", "results"));
    } catch {
      continue;
    }
    const turnId = options.store.listSessionArtifactOutputs(session.id).at(-1)?.version.turnId;
    const visible = new Set(options.store.listSessionArtifactOutputs(session.id).map((output) => output.artifact.name));
    for (const digest of entries) {
      if (!/^[a-f0-9]{12}$/.test(digest)) continue;
      const indexPath = join(root, "antigen_epitope", "results", digest, "index.json");
      let index: unknown;
      try {
        index = JSON.parse(await readFile(indexPath, "utf8"));
      } catch {
        continue;
      }
      const score = index && typeof index === "object" ? (index as { score?: unknown }).score : undefined;
      const description = typeof score === "string" || typeof score === "number"
        ? `screen_score ${score}`.slice(0, 500)
        : "Measured antibody design";
      for (const path of listedPaths(index, digest)) {
        if (visible.has(path)) continue;
        const absolute = join(root, ...path.split("/"));
        let bytes: Buffer;
        try {
          bytes = await readFile(absolute);
        } catch {
          continue;
        }
        if (bytes.length === 0 || bytes.length > MAX_DESIGN_BYTES) continue;
        const { kind, mediaType, title } = kindOf(path);
        const created = await options.store.createArtifactVersion({
          content: await options.put(bytes),
          description,
          kind,
          logicalName: path,
          mediaType,
          origin: "llm_declared",
          originMeta: { role: path.endsWith(".fasta") ? "design-sequence" : "design-structure" },
          sessionId: session.id,
          ...(turnId ? { turnId } : {}),
          sourcePath: path,
          title: `${title} ${digest}`,
        });
        visible.add(path);
        options.notify({ mediaType, sessionId: session.id, version: created.version });
      }
    }
  }
}
