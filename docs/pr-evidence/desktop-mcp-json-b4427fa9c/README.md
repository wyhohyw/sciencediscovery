# Desktop MCP JSON file import — actual UI

Actual desktop app screenshots for openJiuwen-ai/sciencediscovery#1227 and issue #1178.

- Source candidate: `b4427fa9c5fec19660e1395a5c9f81a0ddec2690`.
- Base main: `591e9f477`.
- Capture environment: repository-pinned Playwright Chromium, DPR 1.
- Main captures: 1440 × 1000 CSS px. `*-960.png`: 960 × 720 CSS px.
- Languages: English and Simplified Chinese.
- Data: deterministic Mock API fixture; example local endpoints/paths only, no user records or credentials.
- `*-empty.png`: settings toolbar with Add server / Import.
- `*-http.png`: manual creation defaults, disabled / Streamable HTTP / 60 seconds.
- `*-stdio.png`: manual STDIO configuration, one argument per line.
- `*-import-menu.png` and `*-import-menu-960.png`: Paste JSON / Upload JSON file menu.
- `*-file-preview.png`: actual native file selection auto-fills the existing full-width JSON editor and previews two servers. No server has been saved; confirmation is still required.

All twelve frames were captured from the source candidate above. Historical before/after captures for `f0ca95c5f` remain in the adjacent `desktop-custom-mcp-f0ca95c5` directory with their own provenance. This evidence-only branch does not change the PR's source candidate.

## 中文

这十二张截图来自提交 `b4427fa9c5fec19660e1395a5c9f81a0ddec2690` 的实际桌面应用，中英文各六张，包含导入下拉菜单、上传后的自动填充和预览，以及现有手动新增表单。

主要截图视口为 1440 × 1000，文件名含 `960` 的截图为 960 × 720，DPR 均为 1。使用仓库固定的 Playwright Chromium 和 Mock API 数据，无真实凭据或用户记录。上传图尚未点击“开始导入”，未保存服务器。旧版本图片保留在相邻目录，未冒充本次候选截图。
