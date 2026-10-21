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

import { createTest } from "../../../test/support/tagged/compat.mjs";
const { test } = createTest(import.meta.url, { tags: ["category:ut", "os:linux", "arch:amd64", "arch:arm64"] });
import assert from "node:assert/strict";
import { SettingsApiClient } from "../src/api/settings.js";
test('shipped Settings client sends consent and explicit compatibility mode with normal authentication', async () => {
  const previous = globalThis.fetch, seen: any[] = [];
  globalThis.fetch = async (path, options) => { seen.push([path, options]); return Response.json({ ok: true }); };
  try {
    const client = new SettingsApiClient('synthetic-key'); await client.testModel('a/b'); await client.testModel('a/b', true);
    assert.deepEqual(seen.map(([path, options]) => [path, JSON.parse(options.body), options.headers.authorization]), [['/api/models/a%2Fb/test', { consent: true, compatibility: false }, 'Bearer synthetic-key'], ['/api/models/a%2Fb/test', { consent: true, compatibility: true }, 'Bearer synthetic-key']]);
  } finally { globalThis.fetch = previous; }
});
test('CFG-R14 manual HTTP acknowledgement is serialized for the exact profile only', async () => {
  const previous = globalThis.fetch;
  let body: any;
  globalThis.fetch = async (_path, options) => { body = JSON.parse(options!.body as string); return Response.json({ ok: true }); };
  try {
    const plaintextHttpConsent = { baseUrl: 'http://gateway.invalid/v1', profileUpdatedAt: 'revision-1' };
    await new SettingsApiClient('synthetic-key').testModel('manual-model', false, plaintextHttpConsent);
    assert.deepEqual(body, { consent: true, compatibility: false, plaintextHttpConsent });
  } finally { globalThis.fetch = previous; }
});
