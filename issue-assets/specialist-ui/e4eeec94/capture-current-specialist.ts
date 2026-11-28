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
  const failedId = "idea-tree-team";
  const date = "2026-11-28T00:00:00Z";
  const base = { enabled: true, enabledSkillIds: [], connectorIds: [], runtime: { kind: "native" }, createdAt: date, updatedAt: date };
  const skills = [
    { id: "report-writer", name: "report-writer", source: "built-in", readOnly: true,
      hash: "fixture-report-writer", description: "Synthesize evidence into a research report." },
    { id: failedId, name: failedId, source: "built-in", readOnly: true, hash: "",
      description: "Failed to load: invalid YAML in SKILL.md",
      loadFailure: { code: "SKILL_VALIDATION", message: "Invalid YAML in SKILL.md" } },
  ];
  const builtIns = [
    { id: "assessment-screener", name: "assessment-screener", description: "Evaluate research candidates against configurable scoring rubrics." },
    { id: "code-engineer", name: "code-engineer", description: "Write reproducible analysis code with documented methodology." },
    { id: "literature-scout", name: "literature-scout", description: "Find and summarize relevant publications with source citations." },
  ].map(spec => ({ ...base, ...spec, builtIn: true, instructions: spec.description }));
  const browser = await chromium.launch();
  const frames: Array<{ locale: string; file: string; viewport: { width: number; height: number }; sha256: string }> = [];
  await mkdir(output, { recursive: true });
  try {
    for (const locale of ["zh-CN", "en"] as const) {
      const labels = locale === "en" ? {
        settings: "Settings", specialists: "Specialists", add: "Add specialist", name: "Name",
        description: "Description", instructions: "Instructions", save: "Save", back: "Back",
        detail: "Specialist details", edit: "Edit", editor: "Edit specialist", hint: /Remove the unavailable entries before saving/,
        sample: "Data Review Specialist", summary: "Check research data for missing values and inconsistent records.",
        example: "Confirm the data source. Identify missing values and outliers, then summarize the records that need review.",
      } : {
        settings: "设置", specialists: "专家", add: "新增专家", name: "专家名称",
        description: "描述", instructions: "指令", save: "保存", back: "返回",
        detail: "专家详情", edit: "编辑", editor: "编辑专家", hint: /请移除不可用的条目后再保存/,
        sample: "数据复核专家", summary: "检查研究数据的完整性、缺失值和异常记录，整理可复核的问题清单。",
        example: "先确认数据来源和字段含义。\n检查缺失值、重复记录以及异常范围。\n逐条标明问题所在的列和记录，并给出复核建议。",
      };
      for (const unavailable of [false, true]) {
        const viewport = { width: 1920, height: unavailable ? 1600 : 1080 };
        const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
        try {
          await page.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
          const saved = {
            ...base, id: "evidence-specialist", name: labels.sample, description: labels.summary,
            instructions: labels.example, enabledSkillIds: [failedId, "report-writer"],
          };
          await installMockApi(page, { sessions: [makeSession()], specialists: unavailable ? [saved] : builtIns, skills });
          await page.addInitScript(({ tokenKey, token, localeKey, locale }) => {
            localStorage.setItem(tokenKey, token);
            localStorage.setItem(localeKey, locale);
          }, { tokenKey: TOKEN_STORAGE_KEY, token: TOKEN, localeKey: LOCALE_STORAGE_KEY, locale });
          const capture = async (stem: string, height = viewport.height) => {
            const frame = { width: viewport.width, height };
            await page.setViewportSize(frame);
            await page.mouse.move(5, 5);
            await page.evaluate(() => document.fonts.ready.then(() => undefined));
            const file = `${stem}-${locale}.png`;
            await page.screenshot({ path: `${output}/${file}`, animations: "disabled", caret: "hide" });
            frames.push({ locale, file, viewport: frame, sha256: createHash("sha256").update(await readFile(`${output}/${file}`)).digest("hex") });
          };
          const verifyHint = async (editor: ReturnType<typeof page.getByRole>) => {
            const hints = editor.locator(".specialist-access > button > span");
            await expect(hints).toHaveCount(2);
            for (const hint of await hints.all()) await expect(hint).toHaveCSS("color", "rgb(102, 102, 102)");
          };
          await page.goto(origin);
          await page.getByRole("button", { name: labels.settings, exact: true }).click();
          const settings = page.getByRole("dialog", { name: labels.settings, exact: true });
          await settings.getByRole("button", { name: labels.specialists, exact: true }).click();
          const card = settings.locator(".specialist-card-open").filter({ hasText: labels.sample });
          const detail = settings.getByRole("region", { name: labels.detail, exact: true });
          if (unavailable) {
            await card.click();
          } else {
            await settings.getByRole("button", { name: labels.add, exact: true }).click();
            const create = settings.getByRole("region", { name: labels.add, exact: true });
            await create.getByLabel(labels.name, { exact: true }).fill(labels.sample);
            await create.getByLabel(labels.description, { exact: true }).fill(labels.summary);
            await create.getByLabel(labels.instructions, { exact: true }).fill(labels.example);
            await verifyHint(create);
            await capture("02-create", 1440);
            await create.getByRole("button", { name: labels.save, exact: true }).click();
            await expect(detail.getByRole("heading", { name: labels.sample, exact: true })).toBeVisible();
            const notice = settings.locator(".specialist-notice button");
            if (await notice.isVisible()) await notice.click();
            await capture("03-detail");
          }
          await detail.getByRole("button", { name: labels.edit, exact: true }).click();
          const editor = settings.getByRole("region", { name: labels.editor, exact: true });
          await expect(editor.getByLabel(labels.name, { exact: true })).toHaveValue(labels.sample);
          await verifyHint(editor);
          if (unavailable) {
            const missing = editor.locator(".specialist-capability-card.is-missing");
            await expect(missing).toContainText(failedId);
            await expect(editor.getByRole("status").filter({ hasText: labels.hint })).toBeInViewport();
            await expect(missing).toBeInViewport();
            await expect(editor.getByRole("heading", { name: labels.editor, exact: true })).toBeInViewport();
            await expect(editor.getByRole("button", { name: labels.save, exact: true })).toBeDisabled();
            await capture("05-edit-failed-skill");
            await missing.getByRole("button").click();
            await expect(editor.getByRole("button", { name: labels.save, exact: true })).toBeEnabled();
          } else {
            await capture("04-edit", 1440);
            await editor.getByRole("button", { name: labels.back, exact: true }).click();
            await detail.getByRole("button", { name: labels.back, exact: true }).click();
            await expect(card).toBeInViewport();
            await capture("01-catalog");
          }
        } finally {
          await page.close();
        }
      }
    }
  } finally {
    await browser.close();
  }
  await writeFile(`${output}/manifest.json`, JSON.stringify({
    source, renderer: "Desktop Vite in Chromium", deviceScaleFactor: 1,
    backend: "Isolated local Playwright API fixtures", checkedHintColour: "rgb(102, 102, 102)", frames,
  }, null, 2) + "\n");
}

void main().catch(error => { console.error(error); process.exitCode = 1; });
