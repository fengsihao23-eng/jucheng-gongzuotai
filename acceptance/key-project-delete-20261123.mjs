import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, WEB, OUT, call, loginPage, save, step, token } from './lib.mjs'

for (const origin of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(origin).hostname))
assert.equal((await fetch(API + '/health').then(r => r.json())).env, 'test')
const admin = (await call('admin', 'GET', '/admin/users', { keyword: 'admin' })).items.find(u => u.userid === 'admin')
const name = '右键删除合成验收-' + Date.now()
const project = await call('admin', 'POST', '/market/projects', {
  name, content: '仅在隔离库验证项目删除。', owner_id: admin.id, business_line: 'XY',
  preparation_on: '2026-11-23', due_on: '2026-12-10',
})
const endpoint = `/market/projects/${project.project_code}`
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const desktop = await loginPage(browser, 'admin')
const reader = await loginPage(browser, 'zhengyu')
const mobile = await loginPage(browser, 'admin', { width: 390, height: 844 })
const pages = [desktop, reader, mobile]
for (const page of pages) {
  page.setDefaultTimeout(10000)
  // Connect the local frontend or production preview to the isolated API.
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    const response = await route.fetch({ url: API + url.pathname + url.search })
    await route.fulfill({ response })
  })
}
const card = page => page.getByRole('button', { name: `查看重点项目：${name}`, exact: true })
const menu = page => page.getByRole('menu', { name: '重点项目操作' })
const dialog = page => page.getByRole('dialog', { name: '删除重点项目', exact: true })
const button = (root, label) => root.getByRole('button', { name: label, exact: true })
const deletedRequests = []
desktop.on('request', request => { if (request.method() === 'DELETE') deletedRequests.push(request) })

async function showProject(page) {
  await page.goto(WEB + '/market?tab=key')
  await page.getByRole('textbox', { name: '重点项目搜索' }).fill(name)
  await card(page).waitFor()
}
async function screenshot(page, file) {
  const evidence = `screens/${file}.png`
  await page.screenshot({ path: path.join(OUT, evidence), animations: 'disabled' })
  return { evidence }
}
async function assertInViewport(page, locator) {
  const bounds = await locator.boundingBox()
  const viewport = page.viewportSize()
  assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0)
  assert.ok(bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height)
}

try {
  await step('DELETE-01', '左键仍打开项目，右键显示删除菜单并可关闭', async () => {
    await showProject(desktop)
    await card(desktop).click()
    await desktop.waitForURL(WEB + endpoint)
    await showProject(desktop)
    await card(desktop).click({ button: 'right' })
    await menu(desktop).waitFor()
    assert.equal(new URL(desktop.url()).pathname, '/market')
    await assertInViewport(desktop, menu(desktop))
    const result = await screenshot(desktop, '01-重点项目右键菜单')
    await desktop.keyboard.press('Escape')
    await menu(desktop).waitFor({ state: 'hidden' })
    await card(desktop).click({ button: 'right' })
    await desktop.mouse.click(5, 5)
    await menu(desktop).waitFor({ state: 'hidden' })
    return result
  })
  await step('DELETE-02', '键盘菜单与删除取消保留项目', async () => {
    await card(desktop).focus()
    await desktop.keyboard.press('Shift+F10')
    await menu(desktop).waitFor()
    await desktop.keyboard.press('Enter')
    await dialog(desktop).waitFor()
    await dialog(desktop).getByRole('textbox', { name: '删除密码' }).fill('cancelled')
    await button(dialog(desktop), '取消').click()
    await dialog(desktop).waitFor({ state: 'hidden' })
    assert.equal(deletedRequests.length, 0)
    assert.equal((await call('admin', 'GET', endpoint)).name, name)
  })
  await step('DELETE-03', '错误密码显示提示并允许重试', async () => {
    await card(desktop).click({ button: 'right' })
    await menu(desktop).getByRole('menuitem', { name: '删除项目' }).click()
    await button(dialog(desktop), '确认删除').click()
    await dialog(desktop).getByText('请输入删除密码', { exact: true }).waitFor()
    await dialog(desktop).getByRole('textbox', { name: '删除密码' }).fill('incorrect-delete-password')
    await button(dialog(desktop), '确认删除').click()
    await dialog(desktop).getByText('删除密码不正确，请重新输入', { exact: true }).waitFor()
    assert.equal((await call('admin', 'GET', endpoint)).name, name)
    const result = await screenshot(desktop, '03-删除密码错误提示')
    await button(dialog(desktop), '取消').click()
    await dialog(desktop).waitFor({ state: 'hidden' })
    return result
  })
  await step('DELETE-04', '只读市场账号不出现删除菜单且后端拒绝删除', async () => {
    await showProject(reader)
    await card(reader).click({ button: 'right' })
    assert.equal(await menu(reader).count(), 0)
    const response = await fetch(API + '/api/v1' + endpoint, {
      method: 'DELETE', headers: { Authorization: `Bearer ${await token('zhengyu')}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: process.env.DELETE_PASSWORD || 'admin123' }),
    })
    assert.equal(response.status, 403)
  })
  await step('DELETE-05', '窄屏菜单和删除弹窗完整显示', async () => {
    await showProject(mobile)
    await card(mobile).dispatchEvent('contextmenu', { clientX: 388, clientY: 842, bubbles: true })
    await menu(mobile).waitFor()
    await assertInViewport(mobile, menu(mobile))
    await menu(mobile).getByRole('menuitem', { name: '删除项目' }).click()
    await dialog(mobile).waitFor()
    const result = await screenshot(mobile, '05-窄屏删除确认')
    await assertInViewport(mobile, dialog(mobile).locator('.el-dialog'))
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    await button(dialog(mobile), '取消').click()
    return result
  })
  await step('DELETE-06', '正确密码删除后卡片和统计即时刷新，刷新页面仍不出现', async () => {
    const count = desktop.locator('.kpi').first().locator('.v')
    assert.equal(await count.textContent(), '1')
    await card(desktop).click({ button: 'right' })
    await menu(desktop).getByRole('menuitem', { name: '删除项目' }).click()
    await dialog(desktop).getByRole('textbox', { name: '删除密码' }).fill(process.env.DELETE_PASSWORD || 'admin123')
    await button(dialog(desktop), '确认删除').click()
    await dialog(desktop).waitFor({ state: 'hidden' })
    await card(desktop).waitFor({ state: 'hidden' })
    await desktop.waitForFunction(() => document.querySelector('.kpi .v')?.textContent === '0')
    assert.equal(await count.textContent(), '0')
    const response = await fetch(API + '/api/v1' + endpoint, { headers: { Authorization: `Bearer ${await token('admin')}` } })
    assert.equal(response.status, 404)
    assert.equal(deletedRequests.length, 2)
    for (const request of deletedRequests) {
      assert.equal(new URL(request.url()).search, '')
      assert.deepEqual(Object.keys(request.postDataJSON()), ['password'])
    }
    await desktop.reload()
    await desktop.getByRole('textbox', { name: '重点项目搜索' }).fill(name)
    await desktop.getByText('没有符合条件的项目', { exact: true }).waitFor()
    assert.equal(await card(desktop).count(), 0)
    return screenshot(desktop, '06-删除后列表与统计')
  })
} finally {
  const result = save()
  await browser.close()
  if (result.failed) process.exitCode = 1
}
