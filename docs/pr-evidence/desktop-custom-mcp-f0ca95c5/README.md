# Desktop custom MCP visual evidence

Actual desktop UI captures for PR openJiuwen-ai/sciencediscovery#1227.

- After: candidate `f0ca95c5fcef4a2148f618cec550cad7c1bab49b`.
- Before (`*-before.png`): unchanged main `a5bba117b`.
- Viewport: 1440 × 1000, Chromium using the repository-pinned Playwright environment.
- Languages: English and Simplified Chinese.
- Data: deterministic Mock API fixtures, with no real credentials or user records.
- States: empty MCP list, new HTTP server defaults, and STDIO argument entry.

The after frames are the same captures reviewed locally for the candidate.
The code candidate is unchanged by this evidence-only branch. The frames are
committed here because the original attachment URLs returned HTTP 404 to
reviewers. The PR embeds immutable raw-file URLs to these PNGs.

## 中文

这些是 PR #1227 的实际桌面界面截图，使用固定 Mock API 数据，不含真实凭据或用户记录。
修改后截图对应 `f0ca95c5fcef4a2148f618cec550cad7c1bab49b`；文件名含 `before` 的截图
对应未修改的 main `a5bba117b`。两组均包含中英文界面，用于比较默认超时和 STDIO 参数输入。
