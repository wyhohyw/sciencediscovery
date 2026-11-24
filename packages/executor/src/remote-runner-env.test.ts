// Copyright (C) 2026-2026 Huawei Technologies Co., Ltd
// Licensed under the Apache License, Version 2.0 (the "License");

import assert from "node:assert/strict";
import test from "node:test";

import { remoteRunnerLaunchEnv } from "./remote-compute.js";

test("remote runner launch env publishes OpenDDE settings and the remote bwrap path", () => {
  const env = remoteRunnerLaunchEnv({
    LAYERNORM_TYPE: " torch ",
    OPENDDE_CODE_DIR: "/data/OpenDDE",
    OPENDDE_CHECKPOINT: "",
    SCIENCE_AGENT_SANDBOX_RO_BINDS: "/data/opendde:/opt/py",
    SCIENCE_AGENT_BWRAP_PATH: "/usr/bin/bwrap",
    SCIENCE_AGENT_REMOTE_BWRAP_PATH: "/tmp/cdr-bwrap/bin/bwrap",
  });
  assert.match(env, / LAYERNORM_TYPE='torch'/);
  assert.match(env, / OPENDDE_CODE_DIR='\/data\/OpenDDE'/);
  assert.match(env, / SCIENCE_AGENT_SANDBOX_RO_BINDS='\/data\/opendde:\/opt\/py'/);
  assert.match(env, / SCIENCE_AGENT_BWRAP_PATH='\/tmp\/cdr-bwrap\/bin\/bwrap'/);
  assert.equal(env.includes("/usr/bin/bwrap"), false);
  assert.equal(env.includes("OPENDDE_CHECKPOINT"), false);
});
