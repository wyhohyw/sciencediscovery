import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

async function main() {
  const [runtime, output, expected, origin] = process.argv.slice(2);
  const source = execFileSync("git", ["rev-parse", "HEAD"], { cwd: runtime, encoding: "utf8" }).trim();
  if (source !== expected) throw new Error(`Unexpected checkout ${source}`);
  const url = (path: string) => pathToFileURL(`${runtime}/${path}`).href;
  const { chromium, expect } = await import(url(".e2e/node_modules/@playwright/test/index.mjs"));
  const { installMockApi, makeSession, TOKEN, TOKEN_STORAGE_KEY } = await import(url("apps/desktop/e2e/fixtures.ts"));
  const { LOCALE_STORAGE_KEY } = await import(url("apps/desktop/src/state/storage.ts"));
  const skillId = "idea-tree-team";
  const specialist = {
    id: "evidence-specialist", name: "数据复核专家",
    description: "检查研究数据的完整性、缺失值和异常记录，整理可复核的问题清单。",
    instructions: "先确认数据来源和字段含义。\n检查缺失值、重复记录以及异常范围。\n逐条标明问题所在的列和记录，并给出复核建议。",
    enabled: true, enabledSkillIds: [skillId, "report-writer"], connectorIds: [],
    runtime: { kind: "native" }, createdAt: "2026-11-26T00:00:00Z", updatedAt: "2026-11-26T00:00:00Z",
  };
  const skills = [
    {
      id: skillId, name: skillId, source: "built-in", readOnly: true, hash: "",
      description: "Failed to load: invalid YAML in SKILL.md",
      loadFailure: { code: "SKILL_VALIDATION", message: "Invalid YAML in SKILL.md" },
    },
    {
      id: "report-writer", name: "report-writer", source: "built-in", readOnly: true,
      hash: "fixture-report-writer", description: "Synthesize domain summaries into one report.",
    },
  ];
  const viewport = { width: 1920, height: 1600 };
  const frames: { locale: string; file: string; sha256: string }[] = [];
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch();
  try {
    for (const locale of ["zh-CN", "en"] as const) {
      const labels = locale === "en"
        ? { settings: "Settings", specialists: "Specialists", detail: "Specialist details", edit: "Edit", editor: "Edit specialist", save: "Save", hint: /Remove the unavailable entries before saving/ }
        : { settings: "设置", specialists: "专家", detail: "专家详情", edit: "编辑", editor: "编辑专家", save: "保存", hint: /请移除不可用的条目后再保存/ };
      const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
      try {
        await page.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
        await installMockApi(page, { sessions: [makeSession()], specialists: [specialist], skills });
        await page.addInitScript(({ tokenKey, token, localeKey, locale }) => {
          localStorage.setItem(tokenKey, token);
          localStorage.setItem(localeKey, locale);
        }, { tokenKey: TOKEN_STORAGE_KEY, token: TOKEN, localeKey: LOCALE_STORAGE_KEY, locale });
        await page.goto(origin);
        await page.getByRole("button", { name: labels.settings, exact: true }).click();
        const settings = page.getByRole("dialog", { name: labels.settings, exact: true });
        await settings.getByRole("button", { name: labels.specialists, exact: true }).click();
        await settings.locator(".specialist-card-open").filter({ hasText: specialist.name }).click();
        const detail = settings.getByRole("region", { name: labels.detail, exact: true });
        await detail.getByRole("button", { name: labels.edit, exact: true }).click();
        const editor = settings.getByRole("region", { name: labels.editor, exact: true });
        const failed = editor.locator(".specialist-capability-card.is-missing");
        const healthy = editor.locator(".specialist-capability-card").filter({ hasText: "report-writer" });
        const hint = editor.getByRole("status").filter({ hasText: labels.hint });
        await expect(failed).toHaveCount(1);
        await expect(failed).toContainText(skillId);
        await expect(healthy).toBeVisible();
        await expect(failed.getByRole("button")).toBeEnabled();
        await expect(editor.getByRole("button", { name: labels.save, exact: true })).toBeDisabled();
        await hint.scrollIntoViewIfNeeded();
        await expect(editor.getByRole("heading", { name: labels.editor, exact: true })).toBeInViewport();
        await expect(failed).toBeInViewport();
        await expect(hint).toBeInViewport();
        await page.mouse.move(5, 5);
        await page.evaluate(() => document.fonts.ready.then(() => undefined));
        const file = `05-edit-failed-skill-${locale}.png`;
        await page.screenshot({ path: `${output}/${file}`, animations: "disabled", caret: "hide" });
        frames.push({ locale, file, sha256: createHash("sha256").update(await readFile(`${output}/${file}`)).digest("hex") });
        await failed.getByRole("button").click();
        await expect(failed).toHaveCount(0);
        await expect(editor.getByRole("button", { name: labels.save, exact: true })).toBeEnabled();
      } finally {
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
  await writeFile(`${output}/manifest.json`, JSON.stringify({
    source, renderer: "Desktop Vite in Chromium", viewport, deviceScaleFactor: 1,
    backend: "Local Playwright API fixtures with one saved failed built-in and one healthy skill",
    specialist: specialist.id, failedSkill: skillId, frames,
  }, null, 2) + "\n");
}

void main().catch(error => { console.error(error); process.exitCode = 1; });
