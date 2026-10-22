// Frontend connection/preview/confirmation flow. All API responses are synthetic.
import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const WEB = process.env.WEB || 'http://127.0.0.1:15180'
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(WEB).hostname))
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = []
const writes = []
let attempts = 0
let confirmed = false
let checks = 0
const check = (name, value = true) => { assert.ok(value, name); checks++; console.log('PASS', name) }
const previewId = 'd'.repeat(32)
const profile = { id: 42, userid: 'admin', name: '测试管理员', menus: ['admin', 'portal', 'kb', 'mcp'], buttons: ['*'], roles: [{ code: 'ADMIN-01', name: '平台管理员' }], preferences: {}, is_admin: true, portal: 'employee' }
const user = { id: 101, userid: 'test_employee', name: '预览测试员工', source: '公司通讯录', dept_name: '测试部门', status: 1, roles: ['EMP'] }
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, locale: 'zh-CN' })
  await context.addInitScript(() => localStorage.setItem('jc_token', 'synthetic-test-session'))
  await context.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api/v1', '')
    let data = {}
    if (path === '/auth/me') data = profile
    else if (path === '/notifications') data = { items: [], total: 0, unread: 0 }
    else if (path === '/admin/users') data = { items: confirmed ? [user] : [], total: confirmed ? 1 : 0, directory_total: confirmed ? 1 : 0, departments: [{ code: 'TEST', name: '测试部门' }] }
    else if (path === '/admin/roles') data = { roles: [{ code: 'EMP', name: '普通员工', menus: [], buttons: [], users: confirmed ? 1 : 0 }], menus: {} }
    else if (path === '/admin/addressbook/status') data = { ready: attempts > 1, message: attempts > 1 ? '公司应用已配置' : '公司通讯录尚未接入', scope_note: '只读取公司授权范围内的人员' }
    else if (path === '/assistant/catalog') data = { connectors: [{ key: 'wecom', fields: [{ key: 'wecom_corp_id', value: '' }, { key: 'wecom_agent_id', value: '' }, { key: 'wecom_secret', configured: false }] }] }
    else if (path === '/assistant/connectors/wecom/configure') {
      const body = request.postDataJSON()
      assert.equal(body.values.wecom_agent_id, '1000027')
      assert.equal(body.values.wecom_secret, 'synthetic-test-secret')
      assert.equal(Object.keys(body.values).length, 3)
      attempts++
      data = attempts === 1 ? { ok: false, saved: false, error_code: 60020, server_ip: '192.0.2.7', message: '服务器出口 IP 未授权，配置未保存' } : { ok: true, saved: true, message: '验证成功' }
    } else if (path === '/admin/addressbook/preview') data = { preview_id: previewId, counts: { members: 1, departments: 1, create: 1, update: 0, conflict: 0 }, members: [{ ...user, action: 'create', department_name: '测试部门' }], retire_demo_users: [{ name: '虚构演示', userid: 'demo_example' }], warnings: ['新成员默认普通员工权限'] }
    else if (path === '/admin/addressbook/sync') {
      writes.push(request.postDataJSON())
      assert.deepEqual(writes[0], { preview_id: previewId })
      confirmed = true
      data = { message: '测试人员建档完成', create: 1 }
    } else throw new Error('Unexpected endpoint: ' + path)
    await route.fulfill({ json: { code: 0, data, message: 'ok' } })
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(WEB + '/admin?connect=wecom&agent_id=1000027')
  const connection = page.getByRole('dialog', { name: '接入公司员工通讯录' })
  await connection.waitFor()
  check('直达接入页面并自动带入已知 AgentID', await connection.getByLabel('应用 AgentID', { exact: true }).inputValue() === '1000027')
  await connection.getByLabel('企业 ID（CorpID）', { exact: true }).fill('ww-synthetic-corp')
  await connection.getByLabel('应用 Secret', { exact: true }).fill('synthetic-test-secret')
  await connection.getByRole('button', { name: '验证并读取员工预览' }).click()
  await connection.getByText(/192\.0\.2\.7/).waitFor()
  check('接口明确返回可信 IP 限制后才展示具体 IP', writes.length === 0)
  await connection.getByRole('button', { name: '验证并读取员工预览' }).click()
  const preview = page.getByRole('dialog', { name: '预览公司通讯录' })
  await preview.getByText('预览测试员工', { exact: true }).waitFor()
  check('三项凭证提交后自动打开真实人员变更预览', writes.length === 0)
  await preview.getByText(/归档 1 个演示账号/).waitFor()
  check('演示账号处理范围在确认前显示')
  await preview.getByRole('button', { name: '确认导入公司人员' }).click()
  await preview.waitFor({ state: 'hidden' })
  await page.getByText('已建档 1 名公司成员', { exact: true }).waitFor()
  check('确认后按预览 ID 建档并刷新公司人员来源', writes.length === 1)
  check('整个流程没有组件运行错误', errors.length === 0)
  console.log(`${checks} checks passed`)
} finally { await browser.close() }
