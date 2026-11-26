import { spawn, execFileSync } from "node:child_process";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

async function main() {
  const [runtime, output] = process.argv.slice(2);
  const origin = "http://127.0.0.1:4180";
  const { chromium, expect } = await import(pathToFileURL(`${runtime}/.e2e/node_modules/@playwright/test/index.mjs`).href);
  const { installMockApi, TOKEN_STORAGE_KEY, TOKEN, GUIDE_STORAGE_KEY } = await import(pathToFileURL(`${runtime}/apps/desktop/e2e/fixtures.ts`).href);
  const sha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: runtime, encoding: "utf8" }).trim();
  await mkdir(output, { recursive: true });
  const server = spawn(process.execPath, [`${runtime}/apps/desktop/node_modules/vite/bin/vite.js`, "--host", "127.0.0.1", "--port", "4180", "--strictPort"], { cwd: `${runtime}/apps/desktop`, detached: true, stdio: "ignore", env: { ...process.env, SCIENCE_AGENT_API_URL: "http://127.0.0.1:9" } });
  let browser: Awaited<ReturnType<typeof chromium.launch>> | undefined;
  const images: Array<Record<string, unknown>> = [];
  try {
    await expect.poll(async () => { try { return (await fetch(origin)).ok; } catch { return false; } }, { timeout: 60000 }).toBe(true);
    browser = await chromium.launch();
    for (const locale of ["zh-CN", "en"]) {
      const zh = locale === "zh-CN", label = zh ? "zh" : "en";
      const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
      await context.route("**/*", async route => { const url = new URL(route.request().url()); if (["127.0.0.1", "localhost"].includes(url.hostname)) await route.continue(); else await route.abort(); });
      const page = await context.newPage();
      await page.addInitScript(({ locale, tokenKey, token, guideKey }) => { localStorage.setItem("sciencediscovery-desktop:locale", locale); localStorage.setItem(tokenKey, token); localStorage.setItem(guideKey, "1"); }, { locale, tokenKey: TOKEN_STORAGE_KEY, token: TOKEN, guideKey: GUIDE_STORAGE_KEY });
      const names = zh ? ["文献研究专家", "科研选题专家", "研究方案设计专家", "深度调研与分析专家", "科研绘图专家", "数据分析专家", "科学论文写作专家"] : ["Literature researcher", "Research topic adviser", "Study design specialist", "Research and analysis specialist", "Scientific illustration specialist", "Data analysis specialist", "Scientific writing specialist"];
      const descriptions = zh ? ["全面检索中外相关文献，梳理领域发展脉络，归纳前沿进展，识别当前研究存在的缺口与局限。", "立足前沿、设定有创新、可落地的研究选题，评估可行性与学术价值。", "搭建整体研究框架，规划详细技术路线，确定研究对象与观测指标，设计对照方案。", "整合一手调研记录、行业资料与学术证据，多维比对论证，形成严谨、有洞察力的综合研究结果。", "基于实验数据与研究逻辑绘制统计图、机制示意图，优化版式配色，产出符合期刊规范的科研图表。", "完成数据清洗、预处理与统计建模，解释数据规律，支撑研究结论推导。", "搭建叙事逻辑，按目标期刊要求调整结构与格式，持续修订稿件。"] : ["Search scientific literature, trace developments and identify gaps and limitations in current evidence.", "Develop innovative, feasible research topics grounded in the latest evidence.", "Plan a reproducible study, select measurements and establish suitable controls.", "Combine first-hand records and scientific evidence into a rigorous, traceable research report.", "Create publication-ready scientific charts and mechanism diagrams from experimental data.", "Clean datasets, build statistical models and explain the evidence behind conclusions.", "Build a clear research narrative and revise manuscripts for their target journal."];
      const skill = { id: "research-review", name: zh ? "研究资料审阅" : "Research evidence review", description: zh ? "核对研究数据与文献证据，并逐条记录来源。" : "Check research data and literature evidence with traceable sources.", source: "built-in", readOnly: true };
      const customName = names[3]!;
      const role = (i: number) => ({ id: `ui-role-${i}`, name: names[i], description: descriptions[i], instructions: zh ? "你是科研专家。\n1. 先明确研究问题和预期输出，关键信息不明确时先向用户确认。\n2. 依据可靠来源核对证据，记录数据和文献的来源。\n3. 区分观测事实与推断，不编造数据或引用。\n4. 说明局限，给出可复核的结果和后续建议。" : "You are a research specialist.\n1. Clarify the research question and expected output.\n2. Check evidence and retain traceable sources.\n3. Separate observations from inference. Never invent data or references.\n4. Explain limitations and propose reproducible next steps.", builtIn: [0, 1, 2, 5].includes(i), enabled: ![2, 5, 6].includes(i), runtime: { kind: "native" }, enabledSkillIds: i === 3 || i === 4 || i === 6 ? [skill.id] : [], connectorIds: i === 3 || i === 4 || i === 6 ? ["arxiv"] : [], promptExamples: i === 3 ? [zh ? "请核对这个数据集中的缺失值，并解释可能的影响。" : "Check this dataset for missing values and explain their impact.", zh ? "请比较这两篇论文的方法与结论，并标注证据来源。" : "Compare the methods and conclusions of these two papers, with evidence."] : [], createdAt: "2026-11-26T00:00:00.000Z", updatedAt: "2026-11-26T00:00:00.000Z" });
      await installMockApi(page, { models: [{ id: "model-1", model: "qwen-max", name: "Qwen Max", providerId: "provider-1" }], skills: [skill], connectors: [{ id: "arxiv", displayName: "arXiv", trustLevel: "bundled", commercialUseConstraints: zh ? "搜索开放科学论文与预印本。" : "Search scientific papers and preprints." }], specialists: Array.from({ length: 7 }, (_, i) => role(i)) });
      const settingsName = zh ? "设置" : "Settings", back = zh ? "返回" : "Back";
      await page.goto(origin); await page.getByRole("button", { name: settingsName, exact: true }).click();
      const settings = page.getByRole("dialog", { name: settingsName, exact: true });
      await settings.getByRole("button", { name: zh ? "专家" : "Specialists", exact: true }).click();
      const save = async (state: string) => { const file = `${label}-${state}.png`; await page.screenshot({ path: `${output}/${file}`, animations: "disabled" }); images.push({ file, source: sha, state, locale, viewport: page.viewportSize(), dpr: 1, fixture: "deterministic browser API; no live backend/model", sha256: createHash("sha256").update(await readFile(`${output}/${file}`)).digest("hex") }); };
      if (process.argv[4] === "before") {
        await expect(settings.locator(".settings-card").filter({ hasText: customName })).toBeVisible();
        await save("catalog-1920");
        await settings.getByRole("button", { name: zh ? "新增专家" : "Add specialist", exact: true }).click();
        await expect(page.getByRole("dialog", { name: zh ? "新增专家" : "Add specialist", exact: true })).toBeVisible();
        await save("create-1920"); await page.keyboard.press("Escape");
        for (const width of [1440, 960]) { await page.setViewportSize({ width, height: width === 1440 ? 900 : 720 }); await save(`catalog-${width}`); }
        await context.close(); continue;
      }
      await expect(settings.locator(".specialist-card")).toHaveCount(7); await save("catalog-1920");
      await settings.getByRole("button", { name: zh ? `查看专家「${customName}」` : `View specialist "${customName}"`, exact: true }).click();
      const detail = page.getByRole("region", { name: zh ? "专家详情" : "Specialist details", exact: true });
      await expect(detail).toContainText(customName); await save("detail-1920");
      await detail.getByRole("button", { name: zh ? "编辑" : "Edit", exact: true }).click();
      const form = page.getByRole("region", { name: zh ? "编辑专家" : "Edit specialist", exact: true });
      await expect(form).toBeVisible(); await save("editor-1920");
      await form.getByRole("button", { name: zh ? /配置技能/u : /Configure skills/u }).click();
      let drawer = page.getByRole("dialog", { name: zh ? "配置技能" : "Configure skills", exact: true });
      await expect(drawer.getByRole("checkbox")).toBeChecked(); await save("skills-drawer-1920");
      await drawer.getByRole("button", { name: zh ? "取消" : "Cancel", exact: true }).click();
      await form.getByRole("button", { name: zh ? /配置 MCP 服务/u : /Configure MCP services/u }).click();
      drawer = page.getByRole("dialog", { name: zh ? "配置 MCP 服务" : "Configure MCP services", exact: true });
      await expect(drawer.getByRole("checkbox")).toBeChecked(); await save("mcp-drawer-1920");
      await drawer.getByRole("button", { name: zh ? "取消" : "Cancel", exact: true }).click();
      await form.locator(".specialist-example-field").first().scrollIntoViewIfNeeded(); await save("editor-lower-1920");
      await page.setViewportSize({ width: 960, height: 720 });
      await expect(form.getByRole("button", { name: zh ? "保存" : "Save", exact: true })).toBeInViewport({ ratio: 1 }); await save("editor-960");
      await form.getByRole("button", { name: zh ? /配置技能/u : /Configure skills/u }).click();
      drawer = page.getByRole("dialog", { name: zh ? "配置技能" : "Configure skills", exact: true });
      await expect(drawer.getByRole("button", { name: zh ? "确定" : "Confirm", exact: true })).toBeInViewport({ ratio: 1 }); await save("skills-drawer-960");
      await drawer.getByRole("button", { name: zh ? "取消" : "Cancel", exact: true }).click(); await page.setViewportSize({ width: 1920, height: 1080 });
      await form.getByRole("button", { name: zh ? "取消" : "Cancel", exact: true }).click(); await detail.getByRole("button", { name: back, exact: true }).click();
      await settings.getByRole("button", { name: zh ? "新增专家" : "Add specialist", exact: true }).click(); await save("create-1920");
      await page.getByRole("region", { name: zh ? "新增专家" : "Add specialist", exact: true }).getByRole("button", { name: zh ? "取消" : "Cancel", exact: true }).click();
      for (const width of [1440, 960]) { await page.setViewportSize({ width, height: width === 1440 ? 900 : 720 }); await expect(settings.getByRole("button", { name: zh ? "新增专家" : "Add specialist", exact: true })).toBeInViewport({ ratio: 1 }); await save(`catalog-${width}`); }
      await context.close();
    }
    await writeFile(`${output}/manifest.json`, JSON.stringify({ source: sha, product: "apps/desktop", images }, null, 2));
    process.stdout.write(`Captured ${images.length} UI frames from ${sha}\n`);
  } finally { await browser?.close(); try { process.kill(-server.pid!, "SIGTERM"); } catch {} }
}
main().catch(error => { process.stderr.write(String(error?.stack ?? error) + "\n"); process.exitCode = 1; });
