// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");
export function verifyCdrWorkerQueues(
  bind: (id: string, slot: number) => { cards: number[]; workspaceKey: string },
  epoch: { id: string; sessionId: string },
): Promise<void>;
