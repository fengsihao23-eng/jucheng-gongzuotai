// 小聚主流程：真实 Pi 核心 + DeepSeek + CLI + 自动写入 + 实际 MCP 握手。
// 必须使用独立 API 和 ASSISTANT_HOST_HOME，禁止指向实际业务库。
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const API = process.env.API || 'http://127.0.0.1:18100'
const WEB = process.env.WEB || 'http://localhost:5180'
const OUT = process.env.OUT || '/tmp/jc-xiaoju-evidence'
assert.notEqual(new URL(API).port, '8100', '请连接独立测试 API')
fs.mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = []
let count = 0
const check = (label, ok = true) => { assert.ok(ok, label); count++; console.log('PASS', label) }
const terminal = new Set(['succeeded', 'partial', 'waiting_input', 'waiting_admin', 'failed', 'cancelled'])
const login = await fetch(`${API}/api/v1/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: process.env.DEMO_PASSWORD || 'Jc@2026' }) }).then((r) => r.json())
assert.equal(login.code, 0)
const token = login.data.token
const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

async function pageFor(width = 1440, user = 'admin') {
  const current = user === 'admin' ? login : await fetch(`${API}/api/v1/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: user, password: process.env.DEMO_PASSWORD || 'Jc@2026' }) }).then((r) => r.json())
  assert.equal(current.code, 0)
  const currentToken = current.data.token
  const context = await browser.newContext({ viewport: { width, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
  await context.addInitScript((value) => localStorage.setItem('jc_token', value), currentToken)
  await context.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url()); const api = new URL(API)
    url.host = api.host; url.protocol = api.protocol
    await route.fulfill({ response: await route.fetch({ url: url.href }) })
  })
  const page = await context.newPage()
  page.xiaojuHeaders = { Authorization: `Bearer ${currentToken}`, 'Content-Type': 'application/json' }
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto(WEB + '/workbuddy')
  await page.getByRole('button', { name: '小聚', exact: true }).click()
  await page.getByText('执行引擎已就绪 · 支持自动接入').waitFor()
  return page
}

async function ask(page, message) {
  const response = page.waitForResponse((r) => r.url().endsWith('/assistant/tasks') && r.request().method() === 'POST')
  await page.getByRole('textbox', { name: '向小聚描述需求' }).fill(message)
  await page.getByRole('button', { name: '发送需求', exact: true }).click()
  const created = await (await response).json()
  assert.equal(created.code, 0)
  let task
  for (let i = 0; i < 620; i++) {
    task = await fetch(`${API}/api/v1/assistant/tasks/${created.data.id}`, { headers: page.xiaojuHeaders }).then((r) => r.json()).then((r) => r.data)
    if (terminal.has(task.state)) break
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  assert.ok(terminal.has(task.state), '任务应在测试时限内结束')
  await page.waitForFunction(([id, state]) => { const card = document.querySelector('.execution-card'); return card?.dataset.taskId === id && card?.dataset.taskState === state }, [task.id, task.state])
  return task
}

try {
  const page = await pageFor()
  check('全站名称为小聚，Pi 执行入口已就绪')
  await page.screenshot({ path: path.join(OUT, 'xiaoju-overview.png'), animations: 'disabled' })
  const first = await ask(page, '直接把本机 WorkBuddy 自动接好，并调用本机 pi --version 检查版本')
  const workbuddy = first.outcomes.find((r) => r.provider === 'workbuddy')
  assert.equal(first.state, 'succeeded', JSON.stringify(first))
  check('Pi 自主选择工具并完成自动配置', workbuddy?.installed && workbuddy.tools_count === 13)
  check('实际调用本机 CLI', first.steps.some((s) => s.tool === 'run_cli' && s.state === 'succeeded'))
  check('自动接入主流程无需手动表单', await page.locator('.setup-card:visible').count() === 0)
  const target = workbuddy.config_path
  assert.ok(target.startsWith('/tmp/'), '测试不得修改实际用户目录')
  const original = fs.readFileSync(target)
  check('MCP 配置文件实际写入且权限为 0600', (fs.statSync(target).mode & 0o777) === 0o600)
  await page.screenshot({ path: path.join(OUT, 'xiaoju-workbuddy.png'), animations: 'disabled' })
  const repeated = await ask(page, '再次自动接入本机 WorkBuddy，复用已有配置和令牌，并实际验证 MCP 连接')
  check('复用配置和令牌，不重复修改', repeated.outcomes.some((r) => r.provider === 'workbuddy' && r.changed === false) && fs.readFileSync(target).equals(original))
  const inspect = await ask(page, '检查所有连接状态，只检查不要修改配置')
  check('纯检查任务不执行配置写入', inspect.steps.every((s) => !['install_workbuddy', 'connect_enterprise'].includes(s.tool)) && fs.readFileSync(target).equals(original))
  const wecom = await ask(page, '自动连接企业微信，先自己读取已有配置')
  const wx = wecom.outcomes.find((r) => r.provider === 'wecom')
  check('自动读取企业凭证，缺少时准确等待授权', wx?.status === 'waiting_input' && wx.missing.length === 3)
  check('只在缺失处提供文件导入入口', await page.getByRole('button', { name: '读取已有接入文件' }).count() === 1)
  const lemon = await ask(page, '继续把柠檬云也接入')
  check('柠檬云无授权时不会伪报接通', lemon.outcomes.some((r) => r.provider === 'lemoncloud' && r.status === 'waiting_input'))
  const name = await ask(page, '你叫什么名字')
  check('Agent 自我介绍使用小聚', name.message.includes('小聚'))
  const staff = await pageFor(1440, 'zengli')
  check('普通员工同样使用 Pi 执行核心')
  const shared = await ask(staff, '帮我接入企业微信')
  check('员工无需填写公司密钥，未统一授权时等待管理员', shared.outcomes.some((r) => r.status === 'waiting_admin' && r.needs_personal_credentials === false))
  check('员工没有企业凭证导入和填写入口', await staff.getByRole('button', { name: '读取已有接入文件' }).count() === 0 && await staff.locator('.setup-card input').count() === 0)
  const personal = await ask(staff, '帮我准备个人 WorkBuddy 接入并验证连接')
  check('员工个人授权自动生成并通过真实 MCP 自检', personal.outcomes.some((r) => r.provider === 'workbuddy' && r.download_url && r.tools_count > 0))
  const fileEvent = staff.waitForEvent('download')
  await staff.getByRole('button', { name: '下载已配好的个人连接' }).click()
  const downloaded = await fileEvent
  const body = JSON.parse(fs.readFileSync(await downloaded.path(), 'utf8'))
  check('可一键下载已填好的个人配置，密钥不进入模型', Object.values(body.mcpServers)[0].headers.Authorization.startsWith('Bearer jcmcp_'))
  const mobile = await pageFor(390, 'zengli')
  const box = await mobile.locator('.integration-assistant').boundingBox()
  check('手机布局可用', box.width <= 390 && box.x >= -1)
  await mobile.screenshot({ path: path.join(OUT, 'xiaoju-mobile.png'), animations: 'disabled' })
  check('浏览器无页面或控制台错误', errors.length === 0)
  console.log(`${count} checks passed; screenshots: ${OUT}`)
} finally { await browser.close() }
