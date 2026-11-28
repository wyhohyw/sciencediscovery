import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const pages = [
  { key: "skills", zh: "技能", en: "Skills", selector: ".skill-toolbar" },
  { key: "specialists", zh: "专家", en: "Specialists", selector: ".specialist-toolbar" },
  { key: "mcp", zh: "MCP 服务器", en: "MCP servers", selector: ".mcp-page-toolbar" },
] as const;
const locales = ["zh-CN", "en"] as const;
const widths = [1600, 960, 650] as const;

async function main() {
  const [repo, out, stage, base, head] = process.argv.slice(2);
  const fromRepo = (file: string) => pathToFileURL(`${repo}/${file}`).href;
  const { chromium, expect } = await import(fromRepo(".e2e/node_modules/@playwright/test/index.mjs"));
  const { installMockApi, TOKEN_STORAGE_KEY, TOKEN } = await import(fromRepo("apps/desktop/e2e/fixtures.ts"));
  const { LOCALE_STORAGE_KEY } = await import(fromRepo("apps/desktop/src/state/storage.ts"));
  const browser = await chromium.launch();
  try {
    if (stage === "compare") {
      const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
      const files: string[] = [];
      for (const locale of locales) {
        for (const group of pages) {
          const rows: string[] = [];
          for (const width of widths) {
            rows.push(`<h2>${width}px viewport</h2>`);
            for (const version of ["before", "after"]) {
              const file = `${version}/${locale}/${group.key}-${width}.png`;
              const bytes = await readFile(`${out}/${file}`);
              files.push(file);
              const sha = (version === "before" ? base : head).slice(0, 9);
              rows.push(`<figure><figcaption>${version === "before" ? "Before" : "After"} · ${sha}</figcaption><img src="data:image/png;base64,${bytes.toString("base64")}"></figure>`);
            }
          }
          const label = locale === "en" ? group.en : group.zh;
          await page.setContent(`<!doctype html><meta charset="utf-8"><style>
            body { margin: 0; color: #191919; font: 15px system-ui, sans-serif; }
            main { box-sizing: border-box; width: 1300px; padding: 24px; background: #fafafa; }
            h1 { font-size: 22px; margin: 0 0 20px; }
            h2 { margin: 20px 0 8px; font-size: 16px; }
            figure { margin: 0 0 10px; }
            figcaption { color: #525252; margin: 0 0 3px; }
            img { display: block; max-width: 100%; border: 1px solid #ddd; }
          </style><main><h1>${label} · ${locale}</h1>${rows.join("")}</main>`);
          const file = `${group.key}-${locale}.png`;
          await page.locator("main").screenshot({ path: `${out}/${file}` });
          files.push(file);
        }
      }
      const hashes = await Promise.all(files.sort().map(async file => {
        const sha = createHash("sha256").update(await readFile(`${out}/${file}`)).digest("hex");
        return `${sha}  ${file}`;
      }));
      await writeFile(`${out}/SHA256SUMS`, `${hashes.join("\n")}\n`);
      await writeFile(`${out}/README.md`, `# PR #1556 toolbar screenshots\n\nBefore: ${base}\n\nAfter: ${head}\n\nCaptured with the same desktop Vite renderer and Playwright Chromium fixture data. The screenshots cover Skills, Specialists and MCP at 1600, 960 and 650 px in English and Simplified Chinese. Each comparison image embeds the original toolbar crops. ` +
        "The MCP fixture contains built-in sources only. See SHA256SUMS for all original captures and comparisons.\n");
      return;
    }

    const origin = "http://127.0.0.1:4185";
    for (const locale of locales) {
      const target = `${out}/${stage}/${locale}`;
      await mkdir(target, { recursive: true });
      const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
      await page.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await installMockApi(page, {
        skills: [{ id: "index-papers", name: "index-papers", description: "Index research references.", currentRevision: 1, diagnostics: [], readOnly: true, resourceSummary: { bytes: 10, files: 1, kinds: {} }, source: "built-in" }],
        specialists: [
          { id: "sample-reviewer", name: "Data reviewer", description: "Checks research records.", instructions: "Check the data", enabled: true, enabledSkillIds: ["index-papers"], connectorIds: [], runtime: { kind: "native" } },
          { id: "assessment-screener", name: "assessment-screener", description: "Evaluate candidates.", instructions: "Evaluate candidates", builtIn: true, enabled: true, enabledSkillIds: [], connectorIds: [], runtime: { kind: "native" } },
        ],
        mcpSources: [
          { manifest: { id: "library-search", displayName: "Library search", description: "Search research references.", tools: {} }, status: { status: "ready" } },
          { manifest: { id: "reference-data", displayName: "Reference data", description: "View reference data.", tools: {} }, status: { status: "ready" } },
        ],
      });
      await page.addInitScript(({ key, token, localeKey, language }) => {
        localStorage.setItem(key, token);
        localStorage.setItem(localeKey, language);
      }, { key: TOKEN_STORAGE_KEY, token: TOKEN, localeKey: LOCALE_STORAGE_KEY, language: locale });
      await page.goto(origin);
      const settingsLabel = locale === "en" ? "Settings" : "设置";
      await page.getByRole("button", { name: settingsLabel, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: settingsLabel, exact: true });
      for (const group of pages) {
        await page.setViewportSize({ width: 1600, height: 900 });
        await dialog.getByRole("button", { name: locale === "en" ? group.en : group.zh, exact: true }).click();
        const toolbar = dialog.locator(group.selector);
        await expect(toolbar.getByRole("tablist")).toBeVisible();
        await page.evaluate(() => document.fonts.ready.then(() => undefined));
        await page.mouse.move(2, 2);
        for (const width of widths) {
          await page.setViewportSize({ width, height: 900 });
          await toolbar.screenshot({ path: `${target}/${group.key}-${width}.png`, animations: "disabled" });
        }
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

void main().catch(error => { console.error(error); process.exitCode = 1; });
