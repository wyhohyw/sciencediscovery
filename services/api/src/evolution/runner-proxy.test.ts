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

import { createTest } from "../../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "arch:amd64", "arch:arm64"] });
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import { deflateSync } from "node:zlib";

import type { PermissionEpoch, ShellExecutionRequest } from "@sciencediscovery/schema";

import {
  connectedRunner,
  handleEvolveRunner,
  RunnerGrantRegistry,
  shellForFixedProgram,
  type EvolveRunnerDeps,
} from "./runner-proxy.js";

const EPOCH = {
  createdAt: "2026-01-01T00:00:00.000Z",
  environmentRevisionId: "rev",
  id: "epoch",
  mounts: [],
  networkPolicy: "none",
  reason: "test",
  secretRefs: [],
  sessionId: "session",
} as PermissionEpoch;

function requestOf(method: string, authorization: string | undefined, body?: unknown): IncomingMessage {
  const raw = body === undefined ? [] : [Buffer.from(JSON.stringify(body))];
  return {
    headers: authorization ? { authorization } : {},
    method,
    async *[Symbol.asyncIterator]() {
      yield* raw;
    },
  } as IncomingMessage;
}

function capture(): ServerResponse & { status: number; payload: unknown } {
  const response = {
    payload: undefined as unknown,
    status: 0,
    end(body: string) {
      this.payload = JSON.parse(body);
    },
    writeHead(status: number) {
      this.status = status;
    },
  };
  return response as ServerResponse & { status: number; payload: unknown };
}

function deps(overrides: Partial<EvolveRunnerDeps> = {}): EvolveRunnerDeps & { started: ShellExecutionRequest[] } {
  const started: ShellExecutionRequest[] = [];
  return {
    grants: new RunnerGrantRegistry(),
    runnerClient: () => ({
      cancelShellExecution: async () => ({ state: "cancelled" }),
      getShellExecution: async () => ({ state: "completed", result: { stdout: "{\"device\":\"npu\",\"score\":1}\n" } }),
      shellExecutionLogs: async () => ({ chunks: [], nextCursor: 0 }),
      startShellExecution: async (request) => {
        started.push(request);
        return { state: "queued" };
      },
      snapshotRemoteWorkspace: async () => { throw new Error("no structure fixture"); },
      streamWorkspaceSnapshot: async () => { throw new Error("no structure fixture"); },
      uploadWorkspaceSnapshotFile: async () => {},
      deleteRemoteWorkspace: async () => {},
    }),
    sessions: {
      effectiveRunnerIds: () => ["local", "lab"],
      getRemoteHost: () => ({ workspaceNamespace: "ns" }),
      getSession: () => ({ projectId: "project" }),
      getSessionPermissionEpoch: () => EPOCH,
      npuDeviceSelection: () => [0, 2],
      workspacePath: () => "/data/workspace",
    },
    started,
    ...overrides,
  };
}

test("a conversation with only the local runner is refused", () => {
  assert.deepEqual(connectedRunner(["local"], () => true), {
    error: "this conversation has no remote runner selected",
  });
});

test("the first connected remote runner is the one that scores", () => {
  assert.deepEqual(connectedRunner(["local", "offline", "lab"], (id) => id === "lab"), { runnerId: "lab" });
});

test("a score uses the session's cards and a fresh shell, not the sidecar's devices", async () => {
  const context = deps();
  const token = context.grants.issue("run-1", "session", 30);
  const response = capture();
  const handled = await handleEvolveRunner(
    requestOf("POST", `Bearer ${token}`, {
      code: "print(_EVOLVE_CANDIDATE)\n",
      executionId: "evolve-one",
      npuDevices: [9],
    }),
    response,
    new URL("http://api/internal/evolve-runner/run-1/shell-executions"),
    context,
  );
  assert.equal(handled, true);
  assert.equal(response.status, 202);
  const submitted = context.started[0]!;
  assert.deepEqual(submitted.npuDevices, [0, 2]);
  assert.equal(submitted.permissionEpoch, EPOCH);
  assert.equal(submitted.runnerWorkspaceKey, "project/session/runners/ns");
  assert.equal(submitted.workspaceRoot, "/data/workspace");
  assert.equal(submitted.kernelMode, "ephemeral");
  assert.match(submitted.code, /^python3 -c /);
  assert.equal(
    Buffer.from(submitted.code.split("\n")[1]!, "base64").toString("utf8"),
    "print(_EVOLVE_CANDIDATE)\n",
  );
  assert.equal(shellForFixedProgram("print(1)\n").includes("print(1)"), false);
});

test("CDR5-003 compressed full history exceeding the argv limit executes intact through the proxy", async () => {
  const alphabet = "ACDEFGHIKLMNPQRSTVWY";
  const history = Array.from({ length: 2_000 }, (_, index) => {
    const sequence = "A".repeat(90) + Array.from(randomBytes(30), (byte) => alphabet[byte % alphabet.length]).join("");
    return {
      candidate_id: `node${index + 1}`, parent_id: "node0", cycle: Math.floor(index / 8),
      skill_id: "cdr-full-redesign", chains: { D: sequence }, sequence,
      objective: index / 2_000, structure_id: randomBytes(32).toString("hex"),
      metrics: { iptm: index / 2_000, esm2: -index / 1_000 }, retained: false,
    };
  });
  const note = "引号 ' \"、换行\n、反斜杠 \\、$(exit 23)、`exit 24`、🧬";
  const candidate = JSON.stringify({ analysis_tool: "analyze_evolution_tree", history, note });
  const source = [
    "from __future__ import annotations",
    "import hashlib, json",
    `candidate_json = ${JSON.stringify(candidate)}`,
    "_EVOLVE_CANDIDATE = json.loads(candidate_json)",
    "def annotated(value: UndefinedType): pass",
    'if __name__ == "__main__":',
    '    print(json.dumps({"count": len(_EVOLVE_CANDIDATE["history"]), "digest": hashlib.sha256(candidate_json.encode()).hexdigest(), "note": _EVOLVE_CANDIDATE["note"], "annotation": annotated.__annotations__["value"]}))',
  ].join("\n");
  // The evolve sidecar may already have compressed the program. Its compressed
  // bytes still exceed one Linux argument after the API's base64 encoding.
  const compressed = deflateSync(Buffer.from(source)).toString("base64");
  const program = `import base64, zlib\nexec(compile(zlib.decompress(base64.b64decode("${compressed}")), '<evolve-program>', 'exec'))\n`;
  assert.ok(Buffer.byteLength(Buffer.from(program).toString("base64")) > 128 * 1024);
  const context = deps();
  const token = context.grants.issue("run-long", "session", 30);
  const response = capture();
  await handleEvolveRunner(
    requestOf("POST", `Bearer ${token}`, { code: program, executionId: "evolve-long" }),
    response, new URL("http://api/internal/evolve-runner/run-long/shell-executions"), context,
  );
  assert.equal(response.status, 202);
  const result = spawnSync("/bin/bash", ["--noprofile", "--norc", "-euo", "pipefail", "-s"], {
    input: context.started[0]!.code, encoding: "utf8", timeout: 10_000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    count: history.length, digest: createHash("sha256").update(candidate).digest("hex"),
    note, annotation: "UndefinedType",
  });
});

test("CDR5-003 streamed programs preserve stdout, stderr and failure exit status", () => {
  const result = spawnSync("/bin/bash", ["--noprofile", "--norc", "-euo", "pipefail", "-s"], {
    input: shellForFixedProgram('import sys\nprint("output")\nprint("failure", file=sys.stderr)\nsys.exit(7)\n'),
    encoding: "utf8", timeout: 10_000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 7);
  assert.equal(result.stdout, "output\n");
  assert.equal(result.stderr, "failure\n");
});

test("CDR6-002 capacity and slots use only selected cards and independent worker roots", async () => {
  const context = deps(), token = context.grants.issue("cdr-run", "session", 30);
  const capacity = capture();
  await handleEvolveRunner(requestOf("GET", `Bearer ${token}`), capacity,
    new URL("http://api/internal/evolve-runner/cdr-run/capacity"), context);
  assert.equal(capacity.status, 200);
  assert.deepEqual(capacity.payload, { workers: 2 });
  for (const slot of [0, 1]) {
    const response = capture();
    await handleEvolveRunner(requestOf("POST", `Bearer ${token}`, {
      code: "print(1)", executionId: `fold-${slot}`, cdrDeviceSlot: slot, npuDevices: [9],
    }), response, new URL("http://api/internal/evolve-runner/cdr-run/shell-executions"), context);
    assert.equal(response.status, 202);
  }
  assert.deepEqual(context.started.map(job => job.npuDevices), [[0], [2]]);
  assert.notEqual(context.started[0]!.runnerWorkspaceKey, context.started[1]!.runnerWorkspaceKey);
  assert.ok(context.started.every(job => job.runnerWorkspaceKey!.startsWith("evolve-workers/")));
  context.sessions.npuDeviceSelection = () => [1];
  const changed = capture();
  await handleEvolveRunner(requestOf("POST", `Bearer ${token}`, {
    code: "print(1)", executionId: "new-fold", cdrDeviceSlot: 0,
  }), changed, new URL("http://api/internal/evolve-runner/cdr-run/shell-executions"), context);
  assert.equal(changed.status, 409);
  assert.match(JSON.stringify(changed.payload), /CDR placement changed/);
  const analysis = capture();
  await handleEvolveRunner(requestOf("POST", `Bearer ${token}`, {
    code: "print(1)", executionId: "late-analysis",
  }), analysis, new URL("http://api/internal/evolve-runner/cdr-run/shell-executions"), context);
  assert.equal(analysis.status, 409);
  const status = capture();
  await handleEvolveRunner(requestOf("GET", `Bearer ${token}`), status,
    new URL("http://api/internal/evolve-runner/cdr-run/shell-executions/fold-0"), context);
  assert.equal(status.status, 200);
  for (const slot of [-1, 2, 0.5, "0", true]) {
    context.sessions.npuDeviceSelection = () => [0, 2];
    const bad = capture();
    await handleEvolveRunner(requestOf("POST", `Bearer ${token}`, {
      code: "print(1)", executionId: `bad-${String(slot)}`, cdrDeviceSlot: slot,
    }), bad, new URL("http://api/internal/evolve-runner/cdr-run/shell-executions"), context);
    assert.equal(bad.status, 409);
  }
  assert.equal(context.started.length, 2);
  context.grants.revoke("cdr-run");
});

test("CDR6-003 publish is bound to the owned worker and copies only to the frozen primary", async () => {
  const context = deps(), token = context.grants.issue("run-1", "session", 30);
  const client = context.runnerClient("lab"), copied: string[] = [];
  const structureId = "a".repeat(64), prefix = `.evolve/cdr-artifacts/${structureId}/`;
  context.runnerClient = () => ({ ...client,
    snapshotRemoteWorkspace: async workspace => ({ id: "snapshot", workspace, capturedAt: "now",
      files: ["manifest.json", "summary.json", "full.json", "complex.cif"].map(name => ({
        path: prefix + name, size: 0, sha256: createHash("sha256").digest("hex"), executable: 0,
      })) }),
    streamWorkspaceSnapshot: async () => (async function* () {})(),
    uploadWorkspaceSnapshotFile: async (workspace, path) => {
      assert.equal(workspace, "project/session/runners/ns"); copied.push(path);
    },
  });
  const started = capture();
  await handleEvolveRunner(requestOf("POST", `Bearer ${token}`, {
    code: "print(1)", executionId: "evolve-one", cdrDeviceSlot: 0,
  }), started, endpoint("run-1"), context);
  assert.equal(started.status, 202);
  // Even after the live selection changes, publication follows the accepted job.
  context.sessions.effectiveRunnerIds = () => ["other"];
  const publication = capture();
  await handleEvolveRunner(requestOf("POST", `Bearer ${token}`, { structureId }), publication,
    new URL("http://api/internal/evolve-runner/run-1/shell-executions/evolve-one/publish"), context);
  assert.equal(publication.status, 200);
  assert.equal(copied.length, 4);
  const otherToken = context.grants.issue("run-2", "session", 30), denied = capture();
  await handleEvolveRunner(requestOf("POST", `Bearer ${otherToken}`, { structureId }), denied,
    new URL("http://api/internal/evolve-runner/run-2/shell-executions/evolve-one/publish"), context);
  assert.equal(denied.status, 404);
  assert.equal(copied.length, 4);
  context.grants.revoke("run-1");
});

test("CDR6-004 a token revoked while reading the body cannot dispatch a late worker", async () => {
  const context = deps(), token = context.grants.issue("run-1", "session", 30);
  const request = requestOf("POST", `Bearer ${token}`);
  request[Symbol.asyncIterator] = async function* (): AsyncGenerator<Buffer, undefined> {
    context.grants.revoke("run-1");
    yield Buffer.from(JSON.stringify({ code: "print(1)", executionId: "late", cdrDeviceSlot: 0 }));
    return undefined;
  };
  const response = capture();
  await handleEvolveRunner(request, response, endpoint("run-1"), context);
  assert.equal(response.status, 401); assert.equal(context.started.length, 0);
});

test("no ticked card refuses the score", async () => {
  const context = deps({
    sessions: {
      ...deps().sessions,
      npuDeviceSelection: () => [],
    },
  });
  const token = context.grants.issue("run-1", "session", 30);
  const response = capture();
  await handleEvolveRunner(
    requestOf("POST", `Bearer ${token}`, { code: "print(1)\n", executionId: "evolve-one" }),
    response,
    new URL("http://api/internal/evolve-runner/run-1/shell-executions"),
    context,
  );
  assert.equal(response.status, 409);
  assert.match(String((response.payload as { error: string }).error), /NPU card/);
});

test("a model token is not a runner token", async () => {
  const context = deps();
  const response = capture();
  await handleEvolveRunner(
    requestOf("POST", "Bearer not-the-runner-token", { code: "print(1)\n", executionId: "evolve-one" }),
    response,
    new URL("http://api/internal/evolve-runner/run-1/shell-executions"),
    context,
  );
  assert.equal(response.status, 401);
});

function endpoint(runId: string, executionId?: string, cancel = false): URL {
  return new URL(`http://api/internal/evolve-runner/${runId}/shell-executions${executionId ? `/${executionId}${cancel ? "/cancel" : ""}` : ""}`);
}

async function submit(context: EvolveRunnerDeps, token: string, id = "evolve-one") {
  const response = capture();
  await handleEvolveRunner(requestOf("POST", `Bearer ${token}`, { code: "print(1)\n", executionId: id }), response, endpoint("run-1"), context);
  return response;
}

test("poll and cancel stay on the accepting runner after runner and card selection changes", async () => {
  const calls: string[] = [];
  let selected = ["lab"];
  let cards = [2];
  const context = deps();
  context.sessions.effectiveRunnerIds = () => selected;
  context.sessions.npuDeviceSelection = () => cards;
  context.runnerClient = (id) => ({
    startShellExecution: async (request) => { calls.push(`start:${id}:${request.npuDevices?.join(",")}`); return { state: "queued" }; },
    getShellExecution: async (executionId, owner) => { calls.push(`get:${id}:${executionId}:${owner.sessionId}`); return { state: "running" }; },
    shellExecutionLogs: async () => ({ chunks: [], nextCursor: 0 }),
    cancelShellExecution: async (executionId) => { calls.push(`cancel:${id}:${executionId}`); return { state: "cancelled" }; },
  });
  const token = context.grants.issue("run-1", "session", 30);
  assert.equal((await submit(context, token)).status, 202);
  selected = ["second", "lab"];
  cards = [];
  for (const cancel of [false, true]) {
    const response = capture();
    await handleEvolveRunner(requestOf(cancel ? "POST" : "GET", `Bearer ${token}`), response, endpoint("run-1", "evolve-one", cancel), context);
    assert.equal(response.status, cancel ? 202 : 200);
  }
  assert.deepEqual(calls, ["start:lab:2", "get:lab:evolve-one:session", "cancel:lab:evolve-one"]);
  assert.equal((await submit(context, token, "evolve-two")).status, 409);
});

test("stdout logs stay on the accepting runner and carry the cursor", async () => {
  const calls: Array<{ id: string; cursor: number }> = [];
  const context = deps();
  context.runnerClient = (id) => ({
    startShellExecution: async () => ({ state: "queued" }),
    getShellExecution: async () => ({ state: "running" }),
    shellExecutionLogs: async (_executionId, _owner, cursor = 0) => {
      calls.push({ id, cursor });
      return { chunks: [{ cursor: 2, stream: "stdout", text: "EVOLVE_STAGE protenix\n" }], nextCursor: 2 };
    },
    cancelShellExecution: async () => ({ state: "cancelled" }),
  });
  const token = context.grants.issue("run-1", "session", 30);
  assert.equal((await submit(context, token)).status, 202);
  const response = capture();
  await handleEvolveRunner(
    requestOf("GET", `Bearer ${token}`),
    response,
    new URL("http://api/internal/evolve-runner/run-1/shell-executions/evolve-one/logs?sessionId=session&agentId=evolve&cursor=4"),
    context,
  );
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{ id: "lab", cursor: 4 }]);
  assert.match(JSON.stringify(response.payload), /EVOLVE_STAGE protenix/);
});

test("an uncertain submit keeps its route and cannot be submitted again on a different runner", async () => {
  const calls: string[] = [];
  const context = deps();
  context.runnerClient = (id) => ({
    startShellExecution: async () => { calls.push(`start:${id}`); throw new Error("response lost"); },
    getShellExecution: async () => { calls.push(`get:${id}`); return { state: "running" }; },
    shellExecutionLogs: async () => ({ chunks: [], nextCursor: 0 }),
    cancelShellExecution: async () => ({ state: "cancelled" }),
  });
  const token = context.grants.issue("run-1", "session", 30);
  assert.equal((await submit(context, token)).status, 502);
  context.sessions.effectiveRunnerIds = () => ["second"];
  assert.equal((await submit(context, token)).status, 409);
  const response = capture();
  await handleEvolveRunner(requestOf("GET", `Bearer ${token}`), response, endpoint("run-1", "evolve-one"), context);
  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["start:lab", "get:lab"]);
});

test("another run cannot inspect an execution and a revoked grant loses access", async () => {
  const context = deps();
  const token = context.grants.issue("run-1", "session", 30);
  assert.equal((await submit(context, token)).status, 202);
  const other = context.grants.issue("run-2", "session", 30);
  const response = capture();
  await handleEvolveRunner(requestOf("GET", `Bearer ${other}`), response, endpoint("run-2", "evolve-one"), context);
  assert.equal(response.status, 404);
  context.grants.revoke("run-1");
  await handleEvolveRunner(requestOf("POST", `Bearer ${token}`), response, endpoint("run-1", "evolve-one", true), context);
  assert.equal(response.status, 401);
});

test("an offline accepting runner never redirects an existing job to a different runner", async () => {
  const context = deps();
  const token = context.grants.issue("run-1", "session", 30);
  assert.equal((await submit(context, token)).status, 202);
  context.sessions.effectiveRunnerIds = () => ["second"];
  const requested: string[] = [];
  context.runnerClient = (id) => { requested.push(id); throw new Error("runner offline"); };
  const response = capture();
  await handleEvolveRunner(requestOf("GET", `Bearer ${token}`), response, endpoint("run-1", "evolve-one"), context);
  assert.equal(response.status, 502);
  assert.deepEqual(requested, ["lab"]);
});
