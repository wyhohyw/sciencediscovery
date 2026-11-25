import { spawn, execFileSync } from "node:child_process";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

async function main() {
const [runtime, output, kind, portText] = process.argv.slice(2);
if (!runtime || !output || !["before", "after"].includes(kind)) throw new Error("runtime output before|after port required");
const port = Number(portText);
const origin = `http://127.0.0.1:${port}`;
const { chromium, expect } = await import(pathToFileURL(`${runtime}/.e2e/node_modules/@playwright/test/index.mjs`).href);
const { installMockApi, TOKEN_STORAGE_KEY, TOKEN, GUIDE_STORAGE_KEY } = await import(pathToFileURL(`${runtime}/apps/desktop/e2e/fixtures.ts`).href);
const sha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: runtime, encoding: "utf8" }).trim();
await mkdir(output, { recursive: true });
const server = spawn(process.execPath, [`${runtime}/apps/desktop/node_modules/vite/bin/vite.js`, "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
  cwd: `${runtime}/apps/desktop`, detached: true, stdio: ["ignore", "ignore", "pipe"],
  env: { ...process.env, SCIENCE_AGENT_API_URL: "http://127.0.0.1:9" },
});
let serverErrors = "";
server.stderr.on("data", chunk => { serverErrors += String(chunk); });
const images: Array<Record<string, unknown>> = [];
let browser: Awaited<ReturnType<typeof chromium.launch>> | undefined;
try {
  await expect.poll(async () => {
    try { return (await fetch(origin)).ok; } catch { return false; }
  }, { timeout: 60000 }).toBe(true);
  browser = await chromium.launch();
  for (const locale of ["zh-CN", "en"]) {
    const zh = locale === "zh-CN";
    const label = zh ? "zh" : "en";
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
    await context.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (["127.0.0.1", "localhost"].includes(url.hostname)) await route.continue(); else await route.abort();
    });
    const page = await context.newPage();
    await page.addInitScript(({ tokenKey, token, guideKey, locale }) => {
      localStorage.setItem(tokenKey, token); localStorage.setItem(guideKey, "1");
      localStorage.setItem("sciencediscovery-desktop:locale", locale);
    }, { tokenKey: TOKEN_STORAGE_KEY, token: TOKEN, guideKey: GUIDE_STORAGE_KEY, locale });
    const name = zh ? "测试人员" : "Test Specialist";
    const record = (id: string, roleName: string, description: string, builtIn: boolean) => ({
      id, name: roleName, description, builtIn, enabled: true, connectorIds: [], enabledSkillIds: [],
      instructions: "Check supplied research inputs and report findings.", runtime: { kind: "native" },
      createdAt: "2026-11-25T00:00:00.000Z", updatedAt: "2026-11-25T00:00:00.000Z",
    });
    await installMockApi(page, {
      models: [{ id: "model-1", model: "qwen-max", name: "Qwen Max", providerId: "provider-1" }],
      skills: [], connectors: [], specialists: [
        record("builtin-code-engineer", "code-engineer", "Write and validate reproducible research code.", true),
        record("builtin-literature", "literature-researcher", "Search scientific sources and report evidence with verifiable references.", true),
        record("custom-test-specialist", name, zh ? "专门进行测试，检查研究数据与结果。" : "Validate research data and check the reported results.", false),
      ],
    });
    await page.goto(origin);
    await page.getByRole("button", { name: zh ? "设置" : "Settings", exact: true }).click();
    const settings = page.getByRole("dialog", { name: zh ? "设置" : "Settings", exact: true });
    await settings.getByRole("button", { name: zh ? "专家" : "Specialists", exact: true }).click();
    const row = settings.locator(".settings-card").filter({ hasText: name });
    await expect(row).toBeVisible();
    await row.scrollIntoViewIfNeeded();
    if (kind === "after") {
      await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "true");
      await row.getByRole("button", { name: zh ? `「${name}」的操作` : `Actions for "${name}"`, exact: true }).click();
      await expect(row.getByRole("menuitem")).toHaveText(zh ? ["编辑", "删除"] : ["Edit", "Delete"]);
    } else {
      await expect(row.getByRole("switch")).toHaveCount(0);
      await expect(row.getByRole("button", { name: zh ? "编辑" : "Edit", exact: true })).toBeVisible();
      await expect(row.getByRole("menu")).toHaveCount(0);
    }
    const save = async (suffix: string, clip?: { x: number; y: number; width: number; height: number }) => {
      const file = `${kind}-${label}-${suffix}.png`;
      await page.screenshot({ path: `${output}/${file}`, animations: "disabled", ...(clip ? { clip } : {}) });
      images.push({ file, source: sha, locale, viewport: page.viewportSize(), dpr: 1, fixture: "deterministic API fixture; no live backend/model", sha256: createHash("sha256").update(await readFile(`${output}/${file}`)).digest("hex") });
    };
    await save("full");
    const box = await row.boundingBox();
    const menu = kind === "after" ? await row.getByRole("menu").boundingBox() : undefined;
    const x = Math.max(0, box!.x - 8), y = Math.max(0, Math.min(box!.y, menu?.y ?? box!.y) - 8);
    await save("row", { x, y, width: Math.min(1440 - x, box!.width + 16), height: Math.min(900 - y, Math.max(box!.y + box!.height, menu ? menu.y + menu.height : 0) - y + 8) });
    if (kind === "after") {
      await page.keyboard.press("Escape");
      await page.setViewportSize({ width: 960, height: 720 });
      await row.scrollIntoViewIfNeeded();
      await row.getByRole("button", { name: zh ? `「${name}」的操作` : `Actions for "${name}"`, exact: true }).click();
      for (const item of await row.getByRole("menuitem").all()) await expect(item).toBeInViewport({ ratio: 1 });
      await expect(row.getByRole("switch")).toBeInViewport({ ratio: 1 });
      await save("narrow");
    }
    await context.close();
  }
  await writeFile(`${output}/${kind}-manifest.json`, JSON.stringify({ source: sha, kind, product: "apps/desktop", images }, null, 2));
  process.stdout.write(`${kind}: captured ${images.length} images at ${sha}\n`);
} finally {
  await browser?.close();
  try { process.kill(-server.pid!, "SIGTERM"); } catch {}
  if (server.exitCode !== null && server.exitCode !== 0) process.stderr.write(serverErrors);
}
}
main().catch(error => { process.stderr.write(String(error?.stack ?? error) + "\n"); process.exitCode = 1; });
