// Real Pi + DeepSeek + isolated business DB. Never target the normal API on 8100.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const API = process.env.API || 'http://127.0.0.1:18100'
const WEB = process.env.WEB || 'http://localhost:5180'
const OUT = process.env.OUT || '/tmp/jc-xiaoju-business-evidence'
assert.equal(new URL(API).port, '18100', '请使用独立测试 API 和测试库')
fs.mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const terminal = new Set(['succeeded', 'partial', 'awaiting_confirmation', 'waiting_input', 'waiting_admin', 'failed', 'cancelled'])
const errors = []
const marker = Date.now().toString(36)
let checks = 0
const check = (label, value = true) => { assert.ok(value, label); checks++; console.log('PASS', label) }
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
async function request(url, headers, options = {}) {
  const result = await fetch(API + '/api/v1' + url, { headers, ...options }).then((r) => r.json())
  assert.equal(result.code, 0, result.message)
  return result.data
}
async function pageFor(username = 'admin', width = 1440) {
  const login = await request('/auth/login', { 'Content-Type': 'application/json' }, { method: 'POST', body: JSON.stringify({ username, password: process.env.DEMO_PASSWORD || 'Jc@2026' }) })
  const headers = { Authorization: `Bearer ${login.token}`, 'Content-Type': 'application/json' }
  const context = await browser.newContext({ viewport: { width, height: 1100 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
  await context.addInitScript((value) => localStorage.setItem('jc_token', value), login.token)
  await context.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url()), api = new URL(API)
    url.host = api.host; url.protocol = api.protocol
    await route.fulfill({ response: await route.fetch({ url: url.href }) })
  })
  const page = await context.newPage()
  page.headers = headers
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(WEB + '/')
  await page.getByRole('button', { name: '小聚', exact: true }).click()
  await page.getByText('执行引擎已就绪 · 支持自动接入').waitFor()
  return page
}
async function finish(page, taskId) {
  let task
  for (let count = 0; count < 620; count++) {
    task = await request('/assistant/tasks/' + taskId, page.headers)
    if (terminal.has(task.state)) break
    await pause(500)
  }
  assert.ok(terminal.has(task.state), '任务应在执行时限内结束')
  fs.writeFileSync(path.join(OUT, `${taskId}.json`), JSON.stringify(task, null, 2))
  console.log('TASK', taskId, task.state, task.steps.length, 'steps', task.message.slice(0, 120))
  await page.waitForFunction(() => !document.querySelector('textarea[aria-label="向小聚描述需求"]').disabled, { timeout: 10000 })
  return task
}
async function ask(page, text) {
  const response = page.waitForResponse((r) => r.url().endsWith('/assistant/tasks') && r.request().method() === 'POST')
  await page.getByRole('textbox', { name: '向小聚描述需求' }).fill(text)
  await page.getByRole('button', { name: '发送需求', exact: true }).click()
  const created = await (await response).json()
  assert.equal(created.code, 0, created.message)
  return finish(page, created.data.id)
}
function actual(task) { return (task.business_results || []).filter((item) => item.ok && item.result_id) }

try {
  const page = await pageFor()
  const first = await ask(page, `帮我登记客服工单：客户为小聚端到端-${marker}，问题是教学楼录播主机无法联网，来源渠道电话。登记后查询这张工单，返回工单号。`)
  check('Pi 实际调用工单业务，生成数据库回执', first.state === 'succeeded' && actual(first).some((r) => r.operation_id === 'service.register' || r.operation_id === 'mcp.cs.ticket_register'))
  const tickets = await request(`/service/tickets?keyword=${encodeURIComponent('小聚端到端-' + marker)}`, page.headers)
  check('工单确实写入且只有一张', tickets.total === 1)
  const ticket = tickets.items[0]
  check('聊天展示业务执行回执', await page.getByLabel('业务执行回执').count() > 0)
  await page.getByRole('button', { name: '查看完整结果' }).first().click()
  await page.locator('.result-details').waitFor()
  await page.screenshot({ path: path.join(OUT, 'ticket-created.png'), animations: 'disabled' })
  const follow = await ask(page, '把刚才这张工单状态改成排查中，更新后核对状态。')
  const updated = await request(`/service/tickets/${ticket.id}`, page.headers)
  check('连续对话使用前文编号，更新同一工单', follow.state === 'succeeded' && updated.status === 2 && follow.workflow_id === first.workflow_id)

  const report = await ask(page, '生成 2026-10-20 的每日支出报表，完成后提供生成文件下载。')
  const files = actual(report).flatMap((r) => r.files || [])
  check('Pi 生成实际报表文件', report.state === 'succeeded' && files.some((f) => f.filename.endsWith('.xlsx')))
  for (const file of files) {
    const response = await fetch(new URL(file.url, API))
    assert.equal(response.status, 200)
    assert.ok((await response.arrayBuffer()).byteLength > 1000)
  }
  check('生成文件签名下载可用')
  check('聊天标明演示数据来源', await page.getByText('演示环境数据', { exact: true }).count() > 0)
  await page.screenshot({ path: path.join(OUT, 'report-download.png'), animations: 'disabled' })

  const number = `XJE2E-${marker}`
  const prepared = await ask(page, `新增合同：合同编号 ${number}，合同名称小聚演示采购，客户名称小聚验收学校，金额12000元，签约日期2026-10-20，业务线XY。登记后再查询这份合同核对金额和客户。`)
  check('重要业务操作生成具体确认单并停止 Pi', prepared.state === 'awaiting_confirmation' && prepared.business_actions.length === 1)
  let contracts = await request(`/contracts?keyword=${number}`, page.headers)
  check('点击确认前数据库未写入', contracts.total === 0)
  check('确认单展示实际金额与目标内容', (await page.getByLabel('业务操作确认单').innerText()).includes('12000'))
  await page.screenshot({ path: path.join(OUT, 'contract-confirmation.png'), animations: 'disabled' })
  const continuationResponse = page.waitForResponse((r) => r.url().endsWith('/assistant/tasks') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '确认执行', exact: true }).click()
  const continued = await (await continuationResponse).json()
  assert.equal(continued.code, 0)
  const done = await finish(page, continued.data.id)
  contracts = await request(`/contracts?keyword=${number}`, page.headers)
  check('本人确认后真实写入一次', contracts.total === 1 && Number(contracts.items[0].amount) === 12000)
  check('确认后自动接着处理剩余任务且保留回执', done.state === 'succeeded' && done.workflow_id === prepared.workflow_id && actual(done).some((r) => r.operation_id === 'contracts.create'))
  const repeated = await request(`/assistant/business/actions/${prepared.business_actions[0].id}/confirm`, page.headers, { method: 'POST', body: JSON.stringify({ approve: true }) })
  check('重复确认返回同一回执，不重复登记', repeated.reused === true && (await request(`/contracts?keyword=${number}`, page.headers)).total === 1)

  await page.locator('input[type=file][accept*=".xlsx"]').setInputFiles({ name: '客户清单.csv', mimeType: 'text/csv', buffer: Buffer.from('客户,金额\n南山学校,1200\n海珠学校,2400\n') })
  await page.getByText('客户清单.csv', { exact: true }).waitFor()
  const preview = await ask(page, '读取我上传的附件，预览 CSV 里的客户和金额，不做导入。')
  check('上传的业务文件能由 Pi 实际读取', actual(preview).some((r) => r.operation_id === 'attachments.preview' && r.data.rows.length === 2))

  const staff = await pageFor('zengli', 390)
  const own = await ask(staff, '查询我提交的待办审批，按实际结果回答。')
  check('员工能通过 Pi 查询自己有权限的真实业务记录', own.state === 'succeeded' && actual(own).length > 0)
  const forbidden = await ask(staff, '帮我查询全公司的合同金额。')
  check('员工不能通过对话绕过财务权限', !actual(forbidden).some((r) => r.operation_id.startsWith('contracts.')))
  const box = await staff.locator('.integration-assistant').boundingBox()
  check('业务助手手机布局可用', box.width <= 390 && box.x >= -1)
  await staff.screenshot({ path: path.join(OUT, 'employee-mobile.png'), animations: 'disabled' })
  check('业务组件无浏览器运行错误', errors.length === 0)
  console.log(`${checks} checks passed; screenshots: ${OUT}`)
} finally { await browser.close() }
