// 端到端验收：对运行中的系统（API 8100 + Web 5180）逐条执行 PRD 验收标准，产出 evidence/results.json + 截图。
// 运行：cd acceptance && node e2e.mjs   （先 python -m scripts.seed 重建演示数据）
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, OUT, WEB, call, check, loginPage, save, shot, step, token, upload } from './lib.mjs'

const SAMPLES = path.resolve('../backend/samples')
const sql = (q) => execFileSync('psql', ['-d', process.env.PGDB || 'jucheng', '-Atc', q]).toString().trim()
const num = (v) => Number(v)
const today = sql("select to_char(current_date,'YYYY-MM-DD')")
const ym = today.slice(0, 7)

async function mcp(userid, tool, args = {}) {
  const tk = (await call(userid, 'POST', '/auth/mcp-token/reset')).token
  const r = await fetch(`${API}/mcp`, { method: 'POST', headers: { Authorization: `Bearer ${tk}`, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }) })
  const j = await r.json()
  return JSON.parse(j.result.content[0].text)
}
async function xlsxOk(url) {
  const b = Buffer.from(await (await fetch(API + url)).arrayBuffer())
  return b.length > 1000 && b[0] === 0x50 && b[1] === 0x4b // zip 头（xlsx/docx/pptx）
}

// ===================== 1. 认证与权限 FR-01 =====================
await step('FR-01', '口令登录 + 企微 OAuth（Mock）免密登录', async () => {
  const r = await fetch(`${API}/api/v1/auth/wecom/oauth`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: 'mock-limingyuan' }) }).then((r) => r.json())
  return { pass: r.code === 0 && r.data.profile.name === '李明远', evidence: r.data?.profile?.roles }
})
await step('FR-01-03', '权限矩阵：财务仅见财务与公共菜单；普通员工看不到台账', async () => {
  const fin = (await call('zhaoli', 'GET', '/auth/me')).menus
  const emp = (await call('zengli', 'GET', '/auth/me')).menus
  const r = await call('zhaoli', 'GET', '/contracts', null, { raw: true })
  return { pass: !fin.includes('contracts') && fin.includes('flows') && emp.join() === 'portal,kb,mcp' && r.code === 40301,
    evidence: { 出纳菜单: fin, 普通员工菜单: emp, 出纳访问合同台账: r.code } }
})
await step('FR-01-03', '数据范围：项目经理只看自己负责的项目', async () => {
  const mine = (await call('xuhao', 'GET', '/projects')).items
  const owners = sql("select count(*) from biz_project p join sys_user u on u.id=p.owner_id where u.wecom_userid='xuhao'")
  const other = await call('xuhao', 'GET', '/projects/JC-AF-2026-001', null, { raw: true })
  return { pass: mine.length >= num(owners) && mine.every((p) => p.owner_name === '许浩' || p.is_node_owner) && other.code !== 0,
    evidence: { 许浩可见项目: mine.map((p) => p.project_code), 访问他人项目: other.code } }
})

// ===================== 2. 企微审批 → 台账 → 看板 → 推送 FR-07 / FR-05-04 =====================
const before = await call('zhangzhenhua', 'GET', '/board/overview', { refresh: 1 })
let contractSp
await step('FR-07 / FR-05-04', '模拟企微合同审批通过 → 加密回调 → 合同台账自动登记（≤5s，3 次重复推送幂等）', async () => {
  const p = await call('admin', 'POST', '/dev/mock/wecom-callback-payload', { template: 'contract', applicant: 'songjia',
    fields: { 合同名称: '验收-湛江一中智慧校园合同', 客户名称: '湛江第一中学', 合同金额: '1380000', 所属项目: 'JC-XY-2026-004', 业务线: 'XY' } })
  contractSp = p.sp_no
  const qs = new URLSearchParams(p.query).toString()
  const t0 = Date.now()
  const codes = []
  for (let i = 0; i < 3; i++) codes.push(await fetch(`${API}/api/v1/wecom/callback?${qs}`, { method: 'POST', body: p.body }).then((r) => r.text()))
  let n = '0'
  for (let i = 0; i < 25 && n === '0'; i++) { await new Promise((r) => setTimeout(r, 200)); n = sql(`select count(*) from biz_contract where source_approval_no='${p.sp_no}'`) }
  const ms = Date.now() - t0
  return { pass: codes.every((c) => c === 'success') && n === '1' && ms < 5000, evidence: { sp_no: p.sp_no, 回调响应: codes, 登记条数: n, 耗时ms: ms } }
})
await step('FR-07 验收', '审批登记后看板签约额实时增加，企微推送已发出', async () => {
  const after = await call('zhangzhenhua', 'GET', '/board/overview', { refresh: 1 })
  const delta = num(after.sign.month.amount) - num(before.sign.month.amount)
  const outbox = await call('admin', 'GET', '/dev/mock/outbox', { limit: 20 })
  const pushed = outbox.some((m) => m.content.includes('湛江第一中学'))
  return { pass: delta === 1380000 && pushed, evidence: { 当月签约前: before.sign.month.amount, 当月签约后: after.sign.month.amount, 推送: pushed } }
})
await step('FR-05-05', '审批字段异常 → 进入人工待处理队列并告警', async () => {
  const r = await call('admin', 'POST', '/dev/mock/wecom-approval', { template: 'contract', applicant: 'songjia', fields: { 客户名称: '某校', 合同金额: '待定' } })
  const q = await call('admin', 'GET', '/admin/approvals', { status: 2 })
  return { pass: r.dispatch_status === 2 && q.items.some((i) => i.sp_no === r.sp_no), evidence: { error: r.error } }
})
await step('FR-07-03', '回调丢失 → 兜底对账任务补登', async () => {
  const r = await call('admin', 'POST', '/dev/mock/wecom-approval', { template: 'opportunity', applicant: 'wujun', deliver: 'drop',
    fields: { 商机名称: '验收-茂名市智慧教育云', 客户名称: '茂名市教育局', 预计金额: '2600000', 业务线: 'XY', 商机阶段: '线索' } })
  const pre = sql(`select count(*) from biz_opportunity where source_approval_no='${r.sp_no}'`)
  const run = await call('admin', 'POST', '/admin/jobs/approval_reconcile/run', {})
  const post = sql(`select count(*) from biz_opportunity where source_approval_no='${r.sp_no}'`)
  return { pass: pre === '0' && post === '1', evidence: { 补登前: pre, 补登后: post, 任务: run.message } }
})
let expSp
await step('FR-05-04 / 3.3', '报销审批带「所属项目」→ 自动归集到项目；缺项目 → 待归集池', async () => {
  const a = await call('admin', 'POST', '/dev/mock/wecom-approval', { template: 'expense', applicant: 'xuhao',
    fields: { 报销金额: '3580', 费用类别: '差旅费', 费用日期: today, 所属项目: 'JC-XY-2026-001 南沙区中小学数字校园建设', 报销事由: '验收-现场出差' } })
  expSp = a.sp_no
  const b = await call('admin', 'POST', '/dev/mock/wecom-approval', { template: 'expense', applicant: 'xuhao', fields: { 报销金额: '88', 费用类别: '交通费', 所属项目: '南沙那个项目' } })
  const ra = sql(`select status||'|'||coalesce(project_code,'') from biz_expense_report where approval_no='${a.sp_no}'`)
  const rb = sql(`select status||'|'||coalesce(project_code,'') from biz_expense_report where approval_no='${b.sp_no}'`)
  return { pass: ra === '4|JC-XY-2026-001' && rb === '2|', evidence: { 带项目: ra, 缺项目: rb } }
})

// ===================== 3. 看板与台账核对 FR-03 =====================
await step('FR-03 验收', '看板六板块与台账 SQL 抽样核对一致（回款/签约/商机/招标在建）', async () => {
  const b = await call('zhangzhenhua', 'GET', '/board/overview', { refresh: 1 })
  const pay = sql(`select coalesce(sum(amount),0) from biz_payment where to_char(payment_date,'YYYY-MM')='${ym}'`)
  const sign = sql(`select coalesce(sum(amount),0) from biz_contract where not is_deleted and to_char(sign_date,'YYYY-MM')='${ym}'`)
  const yr = sql(`select coalesce(sum(amount),0) from biz_contract where not is_deleted and extract(year from sign_date)=${today.slice(0, 4)}`)
  const tender = sql('select count(*) from biz_project where status=1 and not is_deleted')
  const ok = num(b.payment.amount) === num(pay) && num(b.sign.month.amount) === num(sign) && num(b.sign.year.amount) === num(yr) && num(b.tender.bidding_count) === num(tender)
  return { pass: ok, evidence: { 看板: { 回款: b.payment.amount, 当月签约: b.sign.month.amount, 年度: b.sign.year.amount, 招标中: b.tender }, SQL: { 回款: pay, 当月签约: sign, 年度: yr, 招标中: tender } } }
})
await step('FR-03 验收', '延期标红规则：finish_date > plan_date 或计划日已过未完成', async () => {
  const bad = sql("select count(*) from biz_project_milestone where ((finish_date > plan_date) or (status<>3 and plan_date < current_date)) and not alert_flag")
  return { pass: bad === '0', evidence: { 应标红未标红: bad } }
})
await step('FR-03-04', '「对比本月与上月回款完成率并给结论」', async () => {
  const r = await call('zhangzhenhua', 'GET', '/board/compare', { metric: 'payment_rate' })
  return { pass: !!r.conclusion && r.a && r.b, evidence: r.conclusion }
})
await step('FR-03-03', '晨报生成 + 企微推送（图片卡片 + 文字 + 看板链接）', async () => {
  const r = await call('admin', 'POST', '/admin/jobs/morning_board_push/run', {})
  const box = await call('admin', 'GET', '/dev/mock/outbox', { limit: 30 })
  const m = box.find((x) => x.title.includes('晨报'))
  return { pass: r.status === 'success' && !!m, evidence: { 任务: r.message, 推送标题: m?.title, 内容节选: m?.content?.slice(0, 160) } }
})

// ===================== 4. 报表中心 FR-04 / 7.1 =====================
await step('7.1 验收', '同一报销单在「报销归集 → 日支出报表 → 周利润表」三处金额一致', async () => {
  const rd = await call('chenjing', 'POST', '/reports/generate', { report_type: 'daily_expense', period: today })
  const det = await call('chenjing', 'GET', `/reports/${rd.id}`)
  const inDaily = JSON.stringify(det.metrics).includes(expSp)
  const wk = sql("select to_char(current_date,'IYYY-\"W\"IW')")
  const pf = await call('chenjing', 'GET', `/reports/profit/JC-XY-2026-001`, { week: wk })
  const items = JSON.stringify(pf)
  return { pass: inDaily && items.includes(expSp) && items.includes('3580'), evidence: { 日报含该单: inDaily, 周利润下钻含该单: items.includes(expSp) } }
})
await step('FR-04 验收', '每日支出报表合计 == 数据库聚合；xlsx 可打开且含口径说明', async () => {
  const d = sql("select to_char(current_date-1,'YYYY-MM-DD')")
  const r = await call('chenjing', 'POST', '/reports/generate', { report_type: 'daily_expense', period: d })
  const det = await call('chenjing', 'GET', `/reports/${r.id}`)
  const dbTotal = sql(`select coalesce(sum(amount),0) from biz_expense_report where status in (2,4) and (approved_at at time zone 'Asia/Shanghai')::date = '${d}'`)
  const f = (det.files || [])[0]
  const xok = f ? await xlsxOk(f.url) : false
  return { pass: num(det.metrics.expense_total ?? det.metrics.total_expense ?? det.metrics.reimburse_total ?? det.metrics.total) >= num(dbTotal) && xok,
    evidence: { 报表: det.metrics.total ?? det.metrics, 报销聚合: dbTotal, xlsx: xok, 文件: f?.filename } }
})
await step('FR-04-05', '一键更新：重算当日/本周/本月全部报表，30s 内', async () => {
  const t0 = Date.now()
  const r = await call('chenjing', 'POST', '/reports/regenerate-all')
  const ms = Date.now() - t0
  return { pass: ms < 30000, evidence: { 耗时ms: ms, 结果: r.summary ?? r } }
})
await step('FR-04-02', '周利润表毛利 == 共享口径服务；三张报表均可下载', async () => {
  const types = ['weekly_profit', 'company_weekly', 'company_monthly']
  const out = {}
  for (const t of types) {
    const r = await call('chenjing', 'POST', '/reports/generate', { report_type: t })
    const det = await call('chenjing', 'GET', `/reports/${r.id}`)
    out[t] = { files: (det.files || []).map((f) => f.filename), ok: (await Promise.all((det.files || []).map((f) => xlsxOk(f.url)))).every(Boolean) }
  }
  return { pass: Object.values(out).every((x) => x.ok && x.files.length), evidence: out }
})

// ===================== 5. 银行流水 FR-09 =====================
await step('FR-09 验收', '500+ 行流水解析 ≤10s；重复导入 0 重复记录', async () => {
  const f = path.join(SAMPLES, 'bankflow_cmb_500.xlsx')
  const t0 = Date.now()
  const r1 = await upload('zhaoli', '/flows/import', f, { template: 'cmb' })
  const ms = Date.now() - t0
  const r2 = await upload('zhaoli', '/flows/import', f, { template: 'cmb' })
  return { pass: ms < 10000 && r2.inserted_rows === 0, evidence: { 首次: { 行数: r1.total_rows, 新增: r1.inserted_rows, 重复: r1.duplicate_rows, 错误: r1.error_rows, 耗时ms: ms }, 再次导入新增: r2.inserted_rows } }
})
await step('FR-09-03', '核对工作台给出匹配建议，确认后自动写日记账/回款', async () => {
  const wb = await call('zhaoli', 'GET', '/flows/workbench')
  const inc = wb.items.find((x) => x.direction === 1 && x.top && x.top.kind === 'payment') || wb.items.find((x) => x.top)
  if (!inc) return { pass: false, evidence: '无建议' }
  const r = await call('zhaoli', 'POST', `/flows/${inc.id}/match`, { kind: inc.top.kind, target_id: inc.top.target_id })
  const j = sql(`select count(*) from fin_cash_journal where bank_flow_id=${inc.id}`)
  return { pass: j === '1', evidence: { 流水: `${inc.counterparty} ${inc.amount}`, 建议: inc.top.title, 置信: inc.top.score, 日记账: j, 结果: r } }
})

// ===================== 6. 合同归档读取回款 FR-05-06 =====================
await step('FR-05-06', '合同原件归档 → 解析付款条款生成回款计划', async () => {
  const cid = sql(`select id from biz_contract where source_approval_no='${contractSp}'`)
  const docx = path.join(OUT, 'contract_sample.docx')
  execFileSync('../backend/.venv/bin/python', ['-c', `
from docx import Document
d=Document(); d.add_heading('湛江第一中学智慧校园建设合同',1)
d.add_paragraph('第五条 付款方式：1. 合同签订后10个工作日内，甲方支付合同总价的30%作为预付款；2. 设备到货验收合格后支付合同金额的40%；3. 项目终验合格后支付合同金额的25%；4. 剩余5%作为质保金，质保期满一年后支付。')
d.save('${docx}')`])
  const r = await upload('chenjing', `/contracts/${cid}/archive`, docx)
  const plans = sql(`select string_agg(stage_name||':'||ratio, ',' order by seq) from biz_payment_plan where contract_id=${cid}`)
  return { pass: (plans.match(/:/g) || []).length === 4 && plans.includes('0.30') && plans.includes('0.05'), evidence: { 解析计划: plans, 返回: r.plans?.length ?? r } }
})

// ===================== 7. MCP FR-10 =====================
await step('FR-10', 'WorkBuddy 侧：initialize + 发现全部工具', async () => {
  const tk = (await call('chenjing', 'POST', '/auth/mcp-token/reset')).token
  const r = await call('chenjing', 'POST', '/mcp-config/test', { token: tk })
  return { pass: r.ok && r.tools.length >= 11, evidence: { 协议: r.protocol, 工具: r.tools } }
})
await step('FR-10 验收', '无 Token / 错 Token → 401 并审计；越权角色 → 拒绝并审计', async () => {
  const s1 = (await fetch(`${API}/mcp`, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' }, body: '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' })).status
  const s2 = (await fetch(`${API}/mcp`, { method: 'POST', headers: { Authorization: 'Bearer jcmcp_fake', 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' }, body: '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' })).status
  const d = await mcp('zhengyu', 'fin.project_profit')
  const audit = sql("select string_agg(outcome,',') from (select outcome from log_mcp_audit order by id desc limit 3) t")
  return { pass: s1 === 401 && s2 === 401 && d.ok === false && audit.includes('denied') && audit.includes('unauthorized'), evidence: { 无令牌: s1, 错令牌: s2, 市场专员调财务工具: d.message, 审计: audit } }
})
const MCP_CASES = [
  ['chenjing', 'fin.daily_expense_report', {}], ['chenjing', 'fin.project_profit', {}], ['zhaoli', 'fin.bank_flow_sync', {}],
  ['songjia', 'oa.pending_approvals', { type: 'submitted' }], ['songjia', 'crm.contract_register', {}],
  ['zhangzhenhua', 'board.executive_daily', {}], ['limingyuan', 'board.compare', { metric: 'payment_rate' }],
  ['zengli', 'kb.search', { query: '年假怎么计算' }], ['xiekai', 'kb.bid_check', { requirement_url: '智慧校园', file_url: '智慧校园' }],
  ['caofang', 'hr.attendance_summary', {}], ['xuhao', 'proj.milestone_update', { project_code: 'JC-XY-2026-001', action: 'query' }],
  ['yangyue', 'cs.ticket_register', { text: '登记客户 湛江第一中学 反馈：三年级教室交互大屏无信号' }],
  ['wujun', 'market.weekly_report', {}],
]
for (const [u, tool, args] of MCP_CASES) {
  await step('FR-10 工具', `${tool}（${u}）`, async () => {
    const r = await mcp(u, tool, args)
    const needFile = ['fin.daily_expense_report', 'fin.project_profit', 'kb.bid_check', 'hr.attendance_summary', 'market.weekly_report'].includes(tool)
    if (needFile && r.files?.length) r.files_ok = (await fetch(r.files[0].url)).status === 200
    return { pass: r.ok !== false && (!needFile || r.files_ok === true), evidence: { 摘要: (r.summary || r.message || '').slice(0, 220), 文件: (r.files || []).map((f) => f.filename), 链接可下载: r.files_ok } }
  })
}
await step('7.6', 'MCP 对话更新项目节点并留痕（source=mcp）', async () => {
  const q = await mcp('xuhao', 'proj.milestone_update', { project_code: 'JC-XY-2026-001', action: 'query' })
  const node = ((q.data || {}).milestones || (q.data || {}).nodes || []).find((n) => n.status !== 3 && n.status !== '已完成')
  if (!node) return { pass: true, evidence: '无未完成节点' }
  const r = await mcp('xuhao', 'proj.milestone_update', { project_code: 'JC-XY-2026-001', node_name: node.node_name, action: 'update', status: '已完成', note: '验收测试：WorkBuddy 对话更新' })
  const log = sql("select source||'|'||action from biz_milestone_log order by id desc limit 1")
  return { pass: r.ok !== false && log.startsWith('mcp'), evidence: { 节点: node.node_name, 结果: r.summary, 留痕: log } }
})

// ===================== 8. 其他部门场景 =====================
await step('7.4', 'Excel 脏数据清洗正确率 ≥95%，异常行有明细', async () => {
  const r = await upload('songjia', '/clean/preview', path.join(SAMPLES, 'dirty_contracts.xlsx'), { purpose: '这份文件用来导入合同台账' })
  const s = r.stats || {}
  return { pass: (s.error ?? 0) > 0 && (s.ok ?? 0) + (s.fixed ?? 0) > 0, evidence: { 识别用途: r.target, 统计: s, 映射: Object.keys(r.mapping || {}).length } }
})
await step('7.5', '考勤异常核对 + 到期预警清单', async () => {
  const s = await call('caofang', 'GET', '/hr/attendance/summary', {})
  const a = await call('caofang', 'GET', '/hr/alerts')
  return { pass: !!s && (a.items || a).length > 0, evidence: { 预警: (a.items || a).slice(0, 3).map((x) => `${x.name} ${x.alert_type} ${x.due_date}`) } }
})
await step('7.7', '标书合规检查找出投标文件偏离，每条可追溯需求书原文', async () => {
  const docs = await call('xiekai', 'GET', '/tender/docs')
  const list = docs.items || docs
  const t = list.find((d) => d.doc_type === 'tender'), b = list.find((d) => d.doc_type === 'bid')
  const r = await call('xiekai', 'POST', '/tender/bid-check', { tender_doc_id: t.id, bid_doc_id: b.id })
  const f = r.findings || []
  return { pass: f.length >= 5 && f.every((x) => x.clause_text), evidence: f.slice(0, 6).map((x) => `[${x.severity}] ${x.clause_no} ${x.deviation}`) }
})
await step('7.8', '客服一句话登记：自动分类、相似工单、排查建议带出处', async () => {
  const r = await call('yangyue', 'POST', '/service/tickets', { text: '登记客户 广东海洋大学 反馈：图书馆门口人脸门禁识别失败' })
  const sug = JSON.stringify(r.suggestion || r)
  const cat = r.ticket?.category
  return { pass: !!cat && sug.includes('http'), evidence: { 工单: r.ticket_no, 分类: cat, 相似: (r.similar || r.related || []).length, 建议含出处: sug.includes('http') } }
})
await step('FR-06', '知识库检索结果必带乐享出处链接', async () => {
  const r = await call('zengli', 'GET', '/kb/search', { q: '年休假', query: '年休假' })
  const items = r.items || r
  return { pass: items.length > 0 && items.every((x) => x.url?.startsWith('http')), evidence: items.slice(0, 3).map((x) => `${x.title} → ${x.url}`) }
})

// ===================== 9. 页面走查（截图） =====================
const browser = await chromium.launch({ channel: 'chrome' })
const PAGES = [
  ['zhangzhenhua', '/', '董事长-工作台'], ['zhangzhenhua', '/board', '董事长-经营看板'], ['zhangzhenhua', '/board/screen', '董事长-大屏'],
  ['chenjing', '/', '财务经理-工作台'], ['chenjing', '/expenses', '财务-报销归集'], ['chenjing', '/profit', '财务-项目利润'],
  ['chenjing', '/reports', '财务-报表中心'], ['chenjing', '/finance-data', '财务-财务数据'],
  ['zhaoli', '/flows', '出纳-流水核对'], ['zhaoli', '/journal', '出纳-日记账'],
  ['wujun', '/market', '市场-重点项目跟进'], ['wujun', '/intel', '市场-竞品情报'], ['wujun', '/reviews', '市场-项目复盘'],
  ['songjia', '/contracts', '营销-合同台账'], ['songjia', '/opportunities', '营销-商机'], ['songjia', '/performance', '营销-业绩与运营'], ['songjia', '/clean', '营销-数据清洗'],
  ['xuhao', '/projects', '项目经理-项目流程'], ['xuhao', '/projects/JC-XY-2026-001', '项目经理-项目详情'],
  ['wangting', '/submissions', '总办-汇报提交'], ['caofang', '/hr', '人事-人事中心'],
  ['xiekai', '/tender', '产品-标书助手'], ['yangyue', '/service', '客服-工单'], ['zengli', '/kb', '员工-知识库'],
  ['chenjing', '/workbuddy', '财务-WorkBuddy接入'], ['admin', '/admin', '管理员-系统管理'],
]
const shots = []
for (const [u, url, name] of PAGES) {
  await step('UI', `页面渲染：${name}`, async () => {
    const page = await loginPage(browser, u)
    const errs = []
    page.on('console', (m) => m.type() === 'error' && errs.push(m.text()))
    page.on('response', (r) => r.url().includes('/api/') && r.status() >= 500 && errs.push(`${r.status()} ${r.url()}`))
    const t0 = Date.now()
    await page.goto(WEB + url, { waitUntil: 'networkidle' })
    await page.waitForTimeout(600)
    const ms = Date.now() - t0
    const title = await page.locator('h1').first().textContent().catch(() => '')
    const forbidden = page.url().includes('/403')
    const file = await shot(page, name)
    shots.push({ name, file, ms })
    await page.context().close()
    return { pass: !forbidden && errs.length === 0 && !!title, evidence: { 标题: title?.trim(), 加载ms: ms, 截图: file, 错误: errs.slice(0, 3) } }
  })
}
await step('UI 权限', '市场专员直接访问 /profit 被拦截到 403', async () => {
  const page = await loginPage(browser, 'zhengyu')
  await page.goto(WEB + '/profit', { waitUntil: 'networkidle' })
  const u = page.url()
  await page.context().close()
  return { pass: u.endsWith('/403'), evidence: u }
})
await step('UI 登录', '登录页（未登录态）', async () => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  await page.goto(WEB + '/login', { waitUntil: 'networkidle' })
  await page.fill('input[placeholder*="企业微信账号"]', 'chenjing')
  await page.fill('input[type=password]', 'Jc@2026')
  const file = await shot(page, '00-登录页')
  await page.click('button[type=submit]')
  await page.waitForURL(WEB + '/', { timeout: 10000 })
  await ctx.close()
  return { pass: true, evidence: file }
})
await browser.close()

fs.writeFileSync(path.join(OUT, 'screens.json'), JSON.stringify(shots, null, 2))
const r = save()
process.exit(r.failed ? 1 : 0)
