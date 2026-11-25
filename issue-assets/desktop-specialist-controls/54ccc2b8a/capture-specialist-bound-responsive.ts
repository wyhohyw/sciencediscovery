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
const { installMockApi, TOKEN_STORAGE_KEY, TOKEN, GUIDE_STORAGE_KEY, makeSession } = await import(pathToFileURL(`${runtime}/apps/desktop/e2e/fixtures.ts`).href);
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
  for (const [locale, width, height] of [["zh-CN", 1440, 900], ["en", 1440, 900], ["zh-CN", 960, 720], ["en", 960, 720]] as const) {
    const zh = locale === "zh-CN";
    const label = `${zh ? "zh" : "en"}${width === 960 ? "-960" : ""}`;
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
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
      id, name: roleName, description, builtIn, enabled: builtIn, connectorIds: [], enabledSkillIds: [],
      instructions: "Check supplied research inputs and report findings.", runtime: { kind: "native" },
      createdAt: "2026-11-25T00:00:00.000Z", updatedAt: "2026-11-25T00:00:00.000Z",
    });
    await installMockApi(page, {
      models: [{ id: "model-1", model: "qwen-max", name: "Qwen Max", providerId: "provider-1" }],
      sessions: [{ ...makeSession(), specialistId: "custom-test-specialist", title: zh ? "停用专家绑定演示" : "Disabled expert binding" }], skills: [], connectors: [], specialists: [
        record("builtin-code-engineer", "code-engineer", "Write and validate reproducible research code.", true),
        record("builtin-literature", "literature-researcher", "Search scientific sources and report evidence with verifiable references.", true),
        record("custom-test-specialist", name, zh ? "专门进行测试，检查研究数据与结果。" : "Validate research data and check the reported results.", false),
      ],
    });
    await page.goto(origin);
    await page.locator(".side-session").filter({ hasText: zh ? "停用专家绑定演示" : "Disabled expert binding" }).first().click();
    await expect(page.locator(".task-head-title")).toContainText(zh ? "停用专家绑定演示" : "Disabled expert binding");
    await page.getByRole("button", { name: zh ? "会话设置" : "Session settings", exact: true }).click();
    const row = page.getByRole("button", { name: zh ? /^专家/u : /^Experts/u });
    await expect(row).toContainText(name);
    await expect(row).toContainText(zh ? "已停用" : "Disabled");
    await row.hover();
    const hint = row.locator(".session-menu-tip");
    await expect(hint).toContainText(zh ? "当前会话仍保留绑定" : "this session keeps its binding");
    await expect(hint).toHaveCSS("opacity", "1");
    await expect(hint).toBeInViewport({ ratio: 1 });
    const save = async (state: string) => {
      const file = `bound-disabled-${label}-${state}.png`;
      await page.screenshot({ path: `${output}/${file}`, animations: "disabled" });
      images.push({ file, source: sha, locale, viewport: page.viewportSize(), dpr: 1, fixture: "deterministic API fixture; bound session and disabled role; no live backend/model", state, sha256: createHash("sha256").update(await readFile(`${output}/${file}`)).digest("hex") });
    };
    await save("hint");
    await row.click();
    const current = page.locator(".expert-row").filter({ hasText: name });
    await expect(current).toHaveAttribute("aria-current", "true");
    await expect(current).toBeDisabled();
    await current.hover();
    await expect(current).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await expect(current.locator(".expert-name")).toHaveCSS("color", "rgb(140, 140, 140)");
    await expect(current).toContainText(zh ? "已停用" : "Disabled");
    await expect(current).toBeInViewport({ ratio: 1 });
    await expect(page.locator(".expert-list").getByRole("button", { name: zh ? "无" : "None", exact: true })).not.toHaveAttribute("aria-current", "true");
    await page.mouse.move(1100, 100);
    await save("flyout");
    await context.close();
  }
  await writeFile(`${output}/bound-manifest.json`, JSON.stringify({ source: sha, kind, product: "apps/desktop", images }, null, 2));
  process.stdout.write(`${kind}: captured ${images.length} images at ${sha}\n`);
} finally {
  await browser?.close();
  try { process.kill(-server.pid!, "SIGTERM"); } catch {}
  if (server.exitCode !== null && server.exitCode !== 0) process.stderr.write(serverErrors);
}
}
main().catch(error => { process.stderr.write(String(error?.stack ?? error) + "\n"); process.exitCode = 1; });
