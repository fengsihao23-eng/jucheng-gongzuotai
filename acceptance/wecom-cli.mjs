// Actual Pi and official WeCom CLI, isolated API/database and personal binding.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { chromium } from 'playwright'
const API = process.env.API || 'http://127.0.0.1:18100'
assert.equal(new URL(API).port, '18100')
const WEB = process.env.WEB || 'http://localhost:5180'
const OUT = '/tmp/jc-wecom-cli-evidence'
fs.mkdirSync(OUT, { recursive: true, mode: 0o700 })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = []
let count = 0
const check = (name, value = true) => { assert.ok(value, name); count++; console.log('PASS', name) }
const terminal = new Set(['succeeded', 'partial', 'failed', 'waiting_input', 'waiting_admin', 'awaiting_confirmation', 'cancelled'])
async function call(path, token, body) {
  const response = await fetch(API + '/api/v1' + path, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) }).then((r) => r.json())
  assert.equal(response.code, 0, response.message)
  return response.data
}
try {
  const { token } = await call('/auth/login', null, { username: 'admin', password: process.env.DEMO_PASSWORD || 'Jc@2026' })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' })
  await context.addInitScript((value) => localStorage.setItem('jc_token', value), token)
  await context.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url()); url.host = new URL(API).host; url.protocol = new URL(API).protocol
    await route.fulfill({ response: await route.fetch({ url: url.href }) })
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(WEB)
  await page.getByRole('button', { name: '修改密码', exact: true }).click()
  await page.getByText('新密码（至少 8 位）', { exact: true }).waitFor()
  check('顶部修改密码入口直达实际表单', page.url().endsWith('/account'))
  await page.getByRole('button', { name: '小聚', exact: true }).click()
  await page.getByText(/企业微信个人连接 · \d+ 项操作可用/).waitFor()
  const catalog = await call('/assistant/catalog', token)
  check('CLI 个人连接按当前登录账号显示', catalog.wecom_cli.available && catalog.wecom_cli.operations > 100)
  async function ask(message) {
    const pending = page.waitForResponse((response) => response.url().endsWith('/assistant/tasks') && response.request().method() === 'POST')
    await page.getByRole('textbox', { name: '向小聚描述需求' }).fill(message)
    await page.getByRole('button', { name: '发送需求', exact: true }).click()
    const id = (await (await pending).json()).data.id
    let task
    for (let i = 0; i < 620; i++) {
      task = await call('/assistant/tasks/' + id, token)
      if (terminal.has(task.state)) break
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
    assert.ok(terminal.has(task.state))
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="向小聚描述需求"]').disabled)
    return task
  }
  const identity = await ask('请通过企业微信 CLI 查询当前绑定身份，只读查询，不发送消息。')
  check('真实 Pi 选择并执行官方 CLI 身份接口', identity.state === 'succeeded' && identity.business_results.some((r) => r.operation_id === 'wecom_cli.identity.whoami' && r.ok))
  check('真实 CLI 回执单独标记 live 来源', identity.business_results.some((r) => r.data_source === 'wecom_cli' && r.data_mode === 'live'))
  await page.getByText('企业微信真实数据 · 个人授权', { exact: true }).waitFor()
  await page.screenshot({ path: OUT + '/personal-connection.png', animations: 'disabled' })
  const search = await ask('通过企业微信 CLI 搜索文档，关键词“小聚接入验证_无匹配文档_20261022”，最多 1 条。请实际查询，缺少功能授权就提示官方授权入口。')
  check('实际业务调用缺授权时明确等待', search.state === 'waiting_input' && search.business_errors.some((e) => e.authorization_url))
  const link = page.getByRole('link', { name: /开通搜索与获取文档内容/ })
  await link.waitFor()
  check('功能授权按钮指向企业微信官方域名', new URL(await link.getAttribute('href')).hostname === 'work.weixin.qq.com')
  await page.screenshot({ path: OUT + '/feature-authorization.png', animations: 'disabled' })
  const staff = await call('/auth/login', null, { username: 'zengli', password: process.env.DEMO_PASSWORD || 'Jc@2026' })
  const own = await call('/assistant/catalog', staff.token)
  check('其他员工不会共享管理员的个人连接', !own.wecom_cli.configured && !own.wecom_cli.available)
  await page.getByRole('button', { name: '关闭小聚', exact: true }).click()
  await page.setViewportSize({ width: 390, height: 900 })
  check('手机宽度保留可见的修改密码入口', await page.getByRole('button', { name: '修改密码', exact: true }).isVisible())
  check('浏览器无组件运行错误', errors.length === 0)
  console.log(`${count} checks passed; ${OUT}`)
} finally { await browser.close() }
