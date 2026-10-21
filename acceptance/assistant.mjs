// 在独立测试 API 上运行，避免重置实际工作台用户的个人令牌。
// API=http://localhost:18100 WEB=http://localhost:5180 node assistant.mjs
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const API = process.env.API || 'http://localhost:18100'
const WEB = process.env.WEB || 'http://localhost:5180'
const OUT = process.env.OUT || '/tmp/jc-assistant-evidence'
fs.mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = []
let checks = 0
const check = (title, condition = true) => { assert.ok(condition, title); checks++; console.log('PASS', title) }

async function pageFor(user, width = 1440) {
  const login = await fetch(`${API}/api/v1/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: user, password: process.env.DEMO_PASSWORD || 'Jc@2026' }) }).then((r) => r.json())
  assert.equal(login.code, 0)
  const context = await browser.newContext({ viewport: { width, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', acceptDownloads: true })
  await context.addInitScript((token) => localStorage.setItem('jc_token', token), login.data.token)
  await context.route('**/api/v1/**', async (route) => {
    const original = new URL(route.request().url())
    const target = new URL(API)
    original.host = target.host
    original.protocol = target.protocol
    await route.fulfill({ response: await route.fetch({ url: original.href }) })
  })
  const page = await context.newPage()
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  await page.goto(WEB)
  await page.getByRole('button', { name: '接入助手', exact: true }).waitFor()
  return page
}

try {
  const admin = await pageFor('admin')
  await admin.goto(`${WEB}/workbuddy`)
  await admin.getByRole('button', { name: '接入助手', exact: true }).click()
  await admin.locator('.connector-tile').last().waitFor()
  check('全站入口和三个系统状态', await admin.locator('.connector-tile').count() === 3)
  await admin.screenshot({ path: path.join(OUT, 'assistant-overview.png'), animations: 'disabled' })
  await admin.getByRole('textbox', { name: '向接入助手描述需求' }).fill('帮我接入 workbuddyy')
  await admin.getByRole('button', { name: '发送需求', exact: true }).click()
  await admin.getByRole('button', { name: '生成并检测连接配置', exact: true }).waitFor()
  await admin.getByRole('button', { name: '生成并检测连接配置', exact: true }).click()
  const confirm = admin.getByRole('button', { name: '重置并生成', exact: true })
  try { await confirm.waitFor({ timeout: 1200 }); await confirm.click() } catch { /* 首次生成无需重置确认 */ }
  await admin.getByText(/服务自检通过 · 发现 \d+ 个工具/).waitFor()
  check('自然语言识别并执行真实 MCP 服务自检')
  const download = admin.waitForEvent('download')
  await admin.getByRole('button', { name: '下载 mcp.json', exact: true }).click()
  const file = await download
  const config = JSON.parse(fs.readFileSync(await file.path(), 'utf8'))
  const server = Object.values(config.mcpServers)[0]
  check('下载可用的 MCP 配置', server.type === 'streamableHttp' && server.headers.Authorization.startsWith('Bearer jcmcp_'))
  check('提示客户端导入仍待完成', (await admin.locator('.connection-result').innerText()).includes('仍待确认'))
  await admin.getByRole('button', { name: '关闭接入助手', exact: true }).click()
  await admin.locator('.integration-assistant').waitFor({ state: 'hidden' })
  await admin.getByRole('button', { name: '接入助手', exact: true }).click()
  await admin.locator('.connector-tile').last().waitFor()
  check('关闭后清除临时个人配置', await admin.locator('.workbuddy-output').count() === 0)

  let chatRequests = 0
  admin.on('request', (r) => { if (r.url().endsWith('/assistant/chat')) chatRequests++ })
  await admin.getByRole('textbox', { name: '向接入助手描述需求' }).fill('Secret=not-a-real-private-value')
  await admin.getByRole('button', { name: '发送需求', exact: true }).click()
  await admin.getByText('这条内容可能含有密钥，请使用接入卡片中的凭证输入框。内容未发送。').waitFor()
  check('疑似密钥未进入请求及聊天历史', chatRequests === 0 && !(await admin.locator('.assistant-scroll').innerText()).includes('not-a-real-private-value'))

  await admin.locator('.connector-tile').filter({ hasText: '企业微信' }).click()
  await admin.getByRole('button', { name: '生成新的回调密钥', exact: true }).waitFor()
  check('管理员可填写企业级配置', await admin.locator('.setup-card .el-input input').count() === 5)
  await admin.getByRole('button', { name: '生成新的回调密钥', exact: true }).click()
  const inputs = admin.locator('.setup-card .el-input input')
  check('生成符合格式的回调密钥', (await inputs.nth(3).inputValue()).length === 32 && (await inputs.nth(4).inputValue()).length === 43)
  await admin.screenshot({ path: path.join(OUT, 'assistant-wecom.png'), animations: 'disabled' })
  await admin.locator('.connector-tile').filter({ hasText: '柠檬云' }).click()
  await admin.getByRole('button', { name: '验证并保存', exact: true }).waitFor()
  check('柠檬云明确区分基础授权和财务同步', (await admin.locator('.setup-card').innerText()).includes('验证保存后保留演示模式'))

  const employee = await pageFor('zengli', 390)
  await employee.getByRole('button', { name: '接入助手', exact: true }).click()
  await employee.locator('.connector-tile').filter({ hasText: '企业微信' }).click()
  await employee.getByText('你可以查看说明。企业级凭证配置与连接检测，请由平台管理员登录后完成。').waitFor()
  check('普通员工看不到企业凭证表单', await employee.locator('.setup-card input').count() === 0)
  const box = await employee.locator('.integration-assistant').boundingBox()
  check('手机宽度抽屉正常适配', box.width <= 390 && box.x >= -1)
  await employee.screenshot({ path: path.join(OUT, 'assistant-mobile.png'), animations: 'disabled' })

  const finance = await pageFor('chenjing')
  await finance.getByRole('button', { name: '接入助手', exact: true }).click()
  await finance.getByRole('button', { name: '报销在哪', exact: false }).click()
  await finance.locator('.message-links').getByRole('button', { name: '报销归集' }).click()
  await finance.waitForURL('**/expenses')
  check('操作引导跳转到有权限的页面')
  assert.deepEqual(errors, [])
  check('浏览器无页面或控制台错误')
  console.log(`${checks} checks passed; screenshots: ${OUT}`)
} finally {
  await browser.close()
}
