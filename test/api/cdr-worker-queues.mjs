// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
/** Offline integration fixture: real Runner admission/leases/CAS, CPU-only operations. */
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { ExecutionManager } from '../../services/runner/dist/execution-manager.js';

export async function verifyCdrWorkerQueues(bind, epoch) {
  const root = await mkdtemp(resolve(tmpdir(), 'cdr-queue-'));
  const manager = new ExecutionManager(root);
  let release, entered, timer, running = 0;
  const gate = new Promise(done => { release = done; });
  const both = new Promise(done => { entered = done; });
  const owner = { agentId: 'evolve', sessionId: epoch.sessionId };
  try {
    for (const slot of [0, 1]) {
      const id = `fold-${slot}`, target = bind(id, slot);
      const workspaceRoot = resolve(root, 'remote-workspaces', target.workspaceKey);
      await mkdir(workspaceRoot, { recursive: true });
      manager.start({ ...owner, executionId: id, code: 'fixture', workspaceRoot,
        permissionEpoch: epoch, npuDevices: target.cards }, async () => {
        if (++running === 2) entered();
        await gate;
        return { executionId: id, environmentRevisionId: 'test', exitCode: 0, stdout: 'folded', stderr: '',
          createdFiles: [], modifiedFiles: [], language: 'shell', kernelMode: 'ephemeral', kernelId: id,
          cgroupMode: 'none', sandbox: 'bubblewrap', environmentVariables: {}, networkPolicy: 'none', runnerVersion: 'test',
          workingDirectory: '/workspace', startedAt: 'now', finishedAt: 'now' };
      });
    }
    await Promise.race([both, new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Runner serialized both workers')), 5_000);
    })]);
    assert.equal(running, 2);
    assert.deepEqual(manager.get('fold-0', owner).npuDevices, [0]);
    assert.deepEqual(manager.get('fold-1', owner).npuDevices, [2]);
  } finally {
    clearTimeout(timer); release();
    await manager.close();
    await rm(root, { recursive: true, force: true });
  }
}
