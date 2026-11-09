// Copyright (C) 2026 Huawei Technologies Co., Ltd
// SPDX-License-Identifier: Apache-2.0
import { randomUUID } from "node:crypto";
import type { ChildProcess } from "node:child_process";
import { expect, type Locator } from "@playwright/test";
import type { ConfigurationRequestView } from "@sciencediscovery/schema";
import { test } from "./helpers/e2e.ts";
import { api, openDesktopSession, seedDesktopOrigin, startDesktopEntry, stopDesktopEntry } from "./helpers/desktop-evolve.ts";
import { cleanupJourney, createProjectAndSession, scriptedModel, type JourneyFixture } from "./helpers/journeys.ts";

const copy = {
  en: { title: "Configure timeouts", preview: "Refresh proposed changes", confirm: "Confirm choice and continue",
    close: "Close without configuring and return to the task", decline: "Do not configure · return to task", saved: "Saved" },
  zh: { title: "调整超时", preview: "刷新修改方案", confirm: "确认选择并继续",
    close: "关闭并暂不配置，返回原任务", decline: "暂不配置，返回任务", saved: "已保存" },
};

// Actual browser hit testing catches narrow cards whose visible controls are
// clipped or covered. No screenshot substitute or mocked API response is used.
async function expectReachable(control: Locator): Promise<void> {
  await control.scrollIntoViewIfNeeded();
  await expect(control).toBeVisible();
  await expect.poll(() => control.evaluate(element => {
    const box = element.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return box.width > 0 && box.x >= -1 && box.right <= innerWidth + 1
      && box.y >= -1 && box.bottom <= innerHeight + 1 && (hit === element || element.contains(hit));
  })).toBe(true);
}

test.describe("Desktop configuration choices", { tag: ["@isolation:global", "@category:e2e", "@os:linux", "@arch:amd64", "@model:mock", "@status:unreviewed"] }, () => {
  let entry: ChildProcess | undefined;
  test.beforeAll(async () => { entry = await startDesktopEntry(); });
  test.afterAll(async () => { await stopDesktopEntry(entry); });

  for (const locale of ["en", "zh"] as const) {
    /**
     * E2E-META
     * Purpose: Desktop users review and save a timeout choice, reload its receipt, and refuse later edits without changing saved settings.
     * Steps:
     *   1. Prepare an owned session and pending card through the real public API; open the desktop entry.
     *   2. Edit at desktop width, resize to narrow width, preview, and confirm the reviewed change.
     *   3. Reload and verify the saved setting and terminal receipt.
     *   4. Close and decline new edited cards; reload and verify neither changed the setting.
     * Environment: Immutable compiled isolated Native API at E2E_BASE_URL; desktop Vite on 4178; pinned Chromium; English and Chinese at 1440x900 and 390x844.
     * Type: mocked
     * LLM: owned loopback stub for model-profile bootstrap only; no run submitted or provider call expected.
     * WebSearch: none
     * PaperSources: none
     * MCP: none
     * OtherExternal: none; non-local browser requests are blocked by the journey fixture.
     * Credentials: E2E_API_TOKEN from the isolated stack; synthetic loopback model token only.
     * CostSideEffects: No paid services; restores isolated timeout settings and deletes owned Project/Session/model records in finally.
     */
    test(`${locale}: review, save and refuse desktop configuration`, { tag: "@mocked" }, async ({ page, journey }) => {
      test.setTimeout(120_000);
      journey.scenario({ goal: "Review a desktop configuration change before saving; closing a later card never saves its edits.",
        preconditions: ["Real compiled isolated API and desktop Vite entry", "No live models or services", `${locale}; desktop and narrow Chromium viewports; DPR 1`] });
      const words = copy[locale];
      const model = await scriptedModel([{ text: "Unexpected model call: this journey submits no run." }]);
      let fixture: JourneyFixture | undefined;
      let baseline: Record<string, number> | undefined;
      let request: ConfigurationRequestView;
      let path = "";
      let next = 0;
      try {
        await journey.step("1. Open a proposed timeout change", "The new desktop shows the pending configuration card in the selected language.", async () => {
          baseline = await api<Record<string, number>>(page, "/api/timeout-settings");
          next = baseline.runnerExecTimeoutMs === 90000 ? 91000 : 90000;
          const name = `Desktop configuration ${locale} ${randomUUID()}`;
          fixture = await createProjectAndSession(page, { projectName: name, sessionTitle: name, model: { apiToken: model.apiToken, baseUrl: model.baseUrl, model: model.model, name } });
          path = `/api/sessions/${encodeURIComponent(fixture.session.id)}/configuration-requests`;
          request = await api<ConfigurationRequestView>(page, path, { method: "POST", data: {
            protocolVersion: 3, capabilityId: "timeouts.configure", values: { runnerExecTimeoutMs: next + 1000 },
          } });
          await seedDesktopOrigin(page);
          await page.addInitScript(language => localStorage.setItem("sciencediscovery-desktop:locale", language), locale === "zh" ? "zh-CN" : "en");
          await page.setViewportSize({ width: 1440, height: 900 });
          await openDesktopSession(page, { ...fixture, modelOwned: true });
          await expect(page.locator(`#configuration-request-${request.id}`).getByRole("heading", { name: words.title, exact: true })).toBeVisible();
        });
        await journey.step("2. Edit without saving, then resize", "The edited value survives a narrow window and confirmation requires a refreshed preview.", async () => {
          const card = page.locator(`#configuration-request-${request.id}`);
          const field = card.locator('input[name="runnerExecTimeoutMs"]');
          await field.fill(String(next));
          await expect(card.getByRole("button", { name: words.confirm, exact: true })).toBeDisabled();
          await page.setViewportSize({ width: 390, height: 844 });
          await expect(field).toHaveValue(String(next));
          await expectReachable(field);
          expect(await api(page, "/api/timeout-settings")).toEqual(baseline);
        });
        await journey.step("3. Review the refreshed change", "The proposed value is visible, confirmation is reachable, and preview has not saved settings.", async () => {
          const card = page.locator(`#configuration-request-${request.id}`);
          await expectReachable(card.getByRole("button", { name: words.preview, exact: true }));
          await card.getByRole("button", { name: words.preview, exact: true }).click();
          await expect(card.getByRole("button", { name: words.confirm, exact: true })).toBeEnabled();
          await expect(card.locator(".configuration-diff")).toContainText(String(next));
          await expectReachable(card.getByRole("button", { name: words.confirm, exact: true }));
          expect(await api(page, "/api/timeout-settings")).toEqual(baseline);
        });
        await journey.step("4. Confirm and reload the saved receipt", "The saved receipt and setting survive reload; the pending card is no longer actionable.", async () => {
          await page.locator(`#configuration-request-${request.id}`).getByRole("button", { name: words.confirm, exact: true }).click();
          await expect.poll(async () => (await api<ConfigurationRequestView>(page, `${path}/${request.id}`)).state).toBe("applied");
          await page.reload();
          const receipt = page.locator(".conversation-configuration-record");
          await expect(receipt).toContainText(words.saved);
          await expect(receipt).toContainText(words.title);
          await expect(page.locator(`#configuration-request-${request.id}`)).toHaveCount(0);
          expect((await api<Record<string, number>>(page, "/api/timeout-settings")).runnerExecTimeoutMs).toBe(next);
        });
        for (const [index, action] of [words.close, words.decline].entries()) {
          await journey.step(`${5 + index}. ${index === 0 ? "Close" : "Decline"} another edited proposal`, "The card records refusal after reload and preserves the previously saved setting.", async () => {
            const saved = await api(page, "/api/timeout-settings");
            request = await api<ConfigurationRequestView>(page, path, { method: "POST", data: {
              protocolVersion: 3, capabilityId: "timeouts.configure", values: { runnerExecTimeoutMs: next + 2000 },
            } });
            await page.reload();
            await page.setViewportSize(index === 0 ? { width: 390, height: 844 } : { width: 1440, height: 900 });
            const card = page.locator(`#configuration-request-${request.id}`);
            await card.locator('input[name="runnerExecTimeoutMs"]').fill(String(next + 3000));
            await expectReachable(card.getByRole("button", { name: action, exact: true }));
            await card.getByRole("button", { name: action, exact: true }).click();
            await expect.poll(async () => (await api<ConfigurationRequestView>(page, `${path}/${request.id}`)).state).toBe("dismissed");
            await page.reload();
            await expect(page.locator(`#configuration-request-${request.id}`)).toHaveCount(0);
            await expect(page.locator(".conversation-configuration-record")).toHaveCount(2 + index);
            expect(await api(page, "/api/timeout-settings")).toEqual(saved);
          });
        }
        expect(model.calls, "No model execution is necessary to configure timeouts").toHaveLength(0);
      } finally {
        try {
          if (baseline) await api(page, "/api/timeout-settings", { method: "PUT", data: baseline });
        } finally {
          try { if (fixture) await cleanupJourney(page, fixture); }
          finally { await model.stop(); }
        }
      }
    });
  }
});
