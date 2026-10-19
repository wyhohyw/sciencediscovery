// Deterministic, offline SVG figures for the product review. No browser or CDN needed.
import { mkdir, writeFile } from 'node:fs/promises';

const output = new URL('../../docs/zh/explanation/assets/conversational-settings/', import.meta.url);
await mkdir(output, { recursive: true });
const c = { ink: '#203a32', muted: '#586d64', line: '#d4e1d8', green: '#246c49', soft: '#edf6ef', blue: '#275e86', blueSoft: '#edf4fa', amber: '#8a6324', amberSoft: '#fff6df', gray: '#f3f5f3' };
const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
function text(x, y, lines, { size = 20, color = c.ink, weight = 400, anchor = 'start', line = 28 } = {}) {
  return `<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${color}" text-anchor="${anchor}">${[].concat(lines).map((value, i) => `<tspan x="${x}" dy="${i ? line : 0}">${esc(value)}</tspan>`).join('')}</text>`;
}
function box(x, y, w, h, title, lines = [], { fill = '#fff', stroke = c.line, titleColor = c.ink, dashed = false, size = 22 } = {}) {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="14" fill="${fill}" stroke="${stroke}" stroke-width="1.6"${dashed ? ' stroke-dasharray="7 5"' : ''}/>`
    + text(x + 22, y + 35, title, { size, weight: 700, color: titleColor })
    + (lines.length ? text(x + 22, y + 66, lines, { size: 18, color: c.muted, line: 26 }) : '');
}
function arrow(points, label, x, y, color = c.green) {
  return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" marker-end="url(#arrow)"/>`
    + (label ? `<g stroke="#fff" stroke-width="6" paint-order="stroke">${text(x, y, label, { size: 18, color, weight: 600 })}</g>` : '');
}
function diamond(x, y, w, h, lines) {
  return `<polygon points="${x},${y - h / 2} ${x + w / 2},${y} ${x},${y + h / 2} ${x - w / 2},${y}" fill="${c.soft}" stroke="${c.green}" stroke-width="1.8"/>`
    + text(x, y - (lines.length > 1 ? 5 : -7), lines, { size: 21, anchor: 'middle', weight: 600, line: 28 });
}
function svg(title, h, content) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="${h}" viewBox="0 0 1040 ${h}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${c.green}"/></marker></defs><rect width="1040" height="${h}" rx="18" fill="#fff"/><g font-family="Microsoft YaHei, PingFang SC, Noto Sans CJK SC, sans-serif">${content}</g></svg>`;
}

const figures = [];

{
  let body = text(36, 44, '把用户的配置负担，收敛为一次明确确认', { size: 28, weight: 700 });
  body += text(36, 76, '同一场景：模型可用，PubMed 已安装且就绪，仅当前会话尚未启用。', { size: 18, color: c.muted });
  body += box(32, 100, 976, 78, '共同起点：用户提出“用 PubMed 查几篇文献”', [], { fill: c.gray, size: 23 });
  body += text(54, 217, '原来 · 配置工作由用户承担', { size: 24, weight: 700, color: c.amber });
  body += text(554, 217, '现在 · 用户只需决定是否启用', { size: 24, weight: 700, color: c.green });
  body += text(54, 247, '离开研究对话，进入设置流程', { size: 18, color: c.muted });
  body += text(554, 247, '留在当前对话，系统承接其余工作', { size: 18, color: c.muted });
  const before = [
    ['用户找入口', '到哪里开启 PubMed？'],
    ['用户认字段、判断范围', '改哪一项？会不会影响其他会话？'],
    ['用户修改并保存', '自己完成启用操作，确认设置生效'],
    ['用户返回对话继续任务', '切回研究上下文，接着完成检索'],
  ];
  for (let i = 0; i < 4; i++) {
    const y = 270 + i * 107;
    body += box(32, y, 476, 88, before[i][0], [before[i][1]], { fill: c.amberSoft, stroke: '#e2ce9c', titleColor: c.amber, size: 21 });
    if (i < 3) body += arrow(`270,${y + 88} 270,${y + 105}`);
  }
  body += box(532, 270, 476, 104, '系统自动准备', ['定位缺项、准备变更，展示原因和范围'], { fill: c.blueSoft, stroke: '#b9cfdf', titleColor: c.blue, dashed: true, size: 20 });
  body += arrow('770,374 770,404');
  body += `<rect x="532" y="408" width="476" height="156" rx="16" fill="${c.green}"/>`;
  body += text(556, 444, '唯一需要用户操作的配置环节', { size: 18, color: '#d9efdf', weight: 600 });
  body += text(770, 495, '确认本次启用', { size: 34, color: '#fff', weight: 700, anchor: 'middle' });
  body += text(770, 534, '范围清楚：仅当前会话', { size: 20, color: '#fff', anchor: 'middle' });
  body += arrow('770,564 770,594');
  body += box(532, 598, 476, 81, '系统写入并接回原任务', ['提供新增工具，继续未完成的研究'], { fill: c.blueSoft, stroke: '#b9cfdf', titleColor: c.blue, dashed: true, size: 20 });
  body += arrow('270,679 270,706 520,706 520,728');
  body += arrow('770,679 770,706 520,706');
  body += box(32, 732, 976, 72, '共同终点：PubMed 在本次会话生效，研究任务继续', [], { fill: c.soft, stroke: c.green, titleColor: c.green, size: 23 });
  body += text(36, 846, '收益：不用找入口、不用辨认字段、不用离开对话；配置控制权仍在用户手中。', { size: 20, weight: 700, color: c.green });
  body += text(36, 882, ['此处比较用户承担的工作，不是固定点击数或耗时统计。', '“一次确认”限于上述就绪场景；需要凭据时仍须补充信息，工具调用权限另行审批。'], { size: 17, line: 26, color: c.muted });
  figures.push(['01-user-effort.svg', svg('图 1：用户承担的配置工作，从自行处理到确认具体变更', 936, body)]);
}

{
  let body = text(36, 44, '一次 PubMed 任务，如何走到结果', { size: 28, weight: 700 });
  body += text(36, 76, '前提：模型可用、PubMed 已安装；本图展示用户确认的主路径。', { size: 18, color: c.muted });
  body += box(270, 106, 500, 88, '用户：用 PubMed 查 3 篇文献', ['由主 Agent 完成，不委派子 Agent'], { fill: c.blueSoft, stroke: '#b9cfdf' });
  body += arrow('520,194 520,222');
  body += diamond(520, 286, 370, 124, ['PubMed 在当前', '会话已启用？']);
  body += arrow('335,286 210,286 210,388', '是', 250, 274);
  body += arrow('705,286 830,286 830,388', '否', 750, 274);
  body += box(40, 390, 340, 106, '直接进入工具执行', ['无需重复配置确认'], { fill: c.soft });
  body += box(660, 390, 340, 106, '卡片说明本次变更', ['仅当前会话启用 PubMed', '原任务保存进度后等待'], { fill: c.amberSoft, stroke: '#e2ce9c' });
  body += arrow('830,496 830,542');
  body += box(660, 544, 340, 108, '用户确认，设置生效', ['回到同一次研究任务', '不要求重新发送原问题'], { fill: c.soft });
  body += arrow('210,496 210,720 520,720 520,752');
  body += arrow('830,652 830,720 520,720');
  body += box(245, 754, 550, 108, '如果工具另需授权，再单独询问', ['配置已启用，不等于实际检索已获权限'], { fill: c.blueSoft, stroke: '#b9cfdf' });
  body += arrow('520,862 520,894');
  body += box(245, 896, 550, 88, '调用 PubMed，给出文献和来源', ['只在研究结束时出现最终答复'], { fill: c.soft, stroke: c.green });
  figures.push(['02-task-flow.svg', svg('图 2：从研究任务到配置确认和结果的主流程', 1010, body)]);
}

{
  let body = text(30, 42, '用户会看到的三个时刻', { size: 28, weight: 700 });
  body += text(30, 74, '卡片示意，非实际截图。第二张权限卡是否出现取决于工具权限策略。', { size: 17, color: c.muted });
  const xs = [24, 368, 712];
  for (let i = 0; i < 3; i++) body += `<rect x="${xs[i]}" y="100" width="304" height="446" rx="16" fill="${c.gray}" stroke="${c.line}"/>`;
  body += text(44, 138, '01 · 等待配置', { size: 22, weight: 700 });
  body += box(40, 160, 272, 72, '用 PubMed 查 3 篇文献', [], { fill: c.blueSoft, size: 18 });
  body += box(40, 248, 272, 218, '启用 PubMed', ['已安装，当前会话未启用', '变更：加入可用连接器', '范围：仅当前会话'], { size: 21, stroke: c.green });
  body += `<rect x="56" y="404" width="240" height="42" rx="7" fill="${c.green}"/>` + text(176, 432, '确认启用并继续', { size: 18, weight: 600, color: '#fff', anchor: 'middle' });
  body += text(44, 507, ['任务状态：等待配置', '此时没有最终研究答复'], { size: 17, color: c.muted, line: 23 });
  body += text(388, 138, '02 · 已处理，原任务继续', { size: 20, weight: 700 });
  body += `<rect x="384" y="160" width="272" height="72" rx="9" fill="${c.soft}" stroke="${c.line}"/><path d="M398 187 l8 6 -8 6" fill="none" stroke="${c.green}" stroke-width="2"/>`;
  body += text(420, 190, ['PubMed 已启用', '仅当前会话 · 可展开详情'], { size: 17, color: c.green, line: 23 });
  body += box(384, 248, 272, 218, '允许这次文献检索？', ['这是工具执行权限', '与启用配置分别决定', '按原权限策略处理'], { size: 19, fill: c.blueSoft, stroke: '#b9cfdf' });
  body += `<rect x="400" y="404" width="240" height="42" rx="7" fill="${c.blue}"/>` + text(520, 432, '允许本次检索', { size: 18, weight: 600, color: '#fff', anchor: 'middle' });
  body += text(388, 507, ['任务状态：已恢复', '或等待独立工具权限'], { size: 17, color: c.muted, line: 23 });
  body += text(732, 138, '03 · 返回研究结果', { size: 22, weight: 700 });
  body += box(728, 160, 272, 306, '检索完成', ['文献 1：标题、年份、来源', '', '文献 2：标题、年份、来源', '', '文献 3：标题、年份、来源', '', '再概括共同研究方向'], { fill: '#fff', size: 21 });
  body += text(732, 507, ['配置记录仍可回看', '最终答复在任务结束后出现'], { size: 17, color: c.muted, line: 23 });
  figures.push(['03-conversation-cards.svg', svg('图 3：配置卡、折叠回执和研究结果示意', 568, body)]);
}

{
  let body = text(36, 44, '暂停的是执行，保留的是同一次任务', { size: 28, weight: 700 });
  body += text(36, 76, '当前已实现范围：原生执行器的已安装连接器启用流程。', { size: 18, color: c.muted });
  body += `<rect x="32" y="108" width="976" height="70" rx="14" fill="${c.soft}" stroke="${c.green}"/>`;
  body += text(520, 152, '同一个任务编号，例如 R-101（示意编号）', { size: 25, color: c.green, weight: 700, anchor: 'middle' });
  const stages = [
    ['已完成的工作', ['接收研究目标', '完成已发出的工具步骤', '保存已提交结果']],
    ['等待配置决定', ['写入恢复记录', '向用户展示配置卡', '不输出最终答复']],
    ['配置确认生效', ['只加入获准连接器', '保留原问题与任务编号', '重新提供可用工具']],
    ['继续未完成工作', ['读回已保存进度', '不重做已提交步骤', '检索后返回最终结果']],
  ];
  stages.forEach(([title, lines], i) => {
    const x = 32 + i * 250;
    body += box(x, 228, 226, 194, title, lines, { size: 21, fill: i === 1 ? c.amberSoft : '#fff' });
    if (i < 3) body += arrow(`${x + 226},325 ${x + 248},325`);
  });
  body += box(32, 464, 976, 112, '“检查点”就是恢复所需的任务记录', ['记录目标、已提交结果与恢复位置；不是恢复任意程序内存，也不判断未知外部动作是否成功。', '最终助手答复只在原研究任务真正结束后生成。'], { fill: c.blueSoft, stroke: '#b9cfdf', size: 23 });
  figures.push(['04-pause-resume.svg', svg('图 4：同一任务暂停和恢复的时间线', 608, body)]);
}

{
  let body = text(36, 44, '30 秒自动应用：先授权，再等待取消机会', { size: 28, weight: 700 });
  body += text(36, 76, '工具的 Always allow 不自动授予配置修改权。当前配置白名单只有 PubMed。', { size: 18, color: c.muted });
  body += diamond(300, 188, 470, 126, ['已开启独立的低风险', '会话配置授权？']);
  body += arrow('300,251 300,294', '是', 320, 280);
  body += diamond(300, 358, 470, 126, ['PubMed 在白名单，且', '安装、就绪等条件满足？']);
  body += arrow('535,188 806,188 806,262', '否', 626, 174);
  body += arrow('535,358 580,358 580,318 630,318', '否', 552, 345);
  body += box(632, 264, 350, 108, '等待用户明确确认', ['不会因等待时间过去而批准'], { fill: c.blueSoft, stroke: '#b9cfdf', size: 22 });
  body += arrow('300,421 300,464', '是', 320, 450);
  body += box(70, 466, 460, 108, '30 秒缓冲，可取消', ['到期由服务端重新检查条件', '不靠用户浏览器保持在线'], { fill: c.amberSoft, stroke: '#e2ce9c' });
  body += arrow('530,520 632,520', '发生变化', 534, 502);
  body += box(634, 466, 348, 108, '停止自动应用', ['取消、Stop、关闭授权', '或设置/连接器条件已变化'], { fill: c.gray, size: 22 });
  body += arrow('300,574 300,620', '条件仍满足', 321, 602);
  body += box(70, 622, 460, 88, '应用配置，接回原任务', ['工具调用仍按自己的权限策略执行'], { fill: c.soft, stroke: c.green });
  figures.push(['05-auto-apply.svg', svg('图 5：低风险自动应用的判断路径', 744, body)]);
}

{
  let body = text(36, 44, '公共流程复用，领域规则分别实现', { size: 28, weight: 700 });
  body += text(36, 76, '目标架构示意：通用公共层尚未完整提取，不能视为全部设置已经接入。', { size: 18, color: c.amber });
  body += box(48, 110, 440, 88, 'Agent：说明任务需要什么', ['只能检查和提案，不能批准自己的变更'], { fill: c.blueSoft, stroke: '#b9cfdf', size: 22 });
  body += box(552, 110, 440, 88, '用户：补缺项并决定', ['确认具体变更、验证动作与影响范围'], { fill: c.blueSoft, stroke: '#b9cfdf', size: 22 });
  body += arrow('268,198 268,238') + arrow('772,198 772,238');
  body += box(48, 240, 944, 170, '目标公共配置服务', ['共用：卡片与输入描述 → 变更预览 → 用户决定 → 验证与保存', '共用：结果记录、重复操作处理，以及新配置生效和任务恢复协调', '正式设置仍写入原来的领域存储，不另存一套配置值。'], { fill: c.amberSoft, stroke: '#bf9854', dashed: true, size: 25 });
  const adapters = [
    [48, '模型适配器', ['协议、地址、凭据', '模型发现、验证与绑定']],
    [374, '连接器适配器', ['已安装与就绪检查', '会话启用与工具重建']],
    [700, '其他设置适配器', ['例如超时、代理、Runner', '按类型接入，尚待实现']],
  ];
  adapters.forEach(([x, title, lines]) => {
    body += arrow(`${x + 146},410 ${x + 146},452`);
    body += box(x, 454, 292, 114, title, lines, { fill: title === '连接器适配器' ? c.soft : '#fff', size: 22 });
    body += arrow(`${x + 146},568 ${x + 146},606`);
  });
  body += box(48, 608, 944, 90, '复用已有设置、模型和凭据存储', ['每个适配器负责领域校验与影响范围，公共流程负责统一组织。'], { fill: c.soft, stroke: c.green, size: 23 });
  figures.push(['06-shared-architecture.svg', svg('图 6：公共配置机制与领域适配器分工', 732, body)]);
}

for (const [name, content] of figures) await writeFile(new URL(name, output), content + '\n', 'utf8');
console.log(`Generated ${figures.length} review figures in ${output.pathname}`);
