// Browser and real model in an isolated test service. Never touches production data.
import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, WEB, OUT, call, loginPage } from './lib.mjs'

assert.equal(new URL(API).hostname, '127.0.0.1')
assert.equal((await fetch(`${API}/health`).then(r => r.json())).env, 'test')
assert.equal((await call('admin', 'GET', '/assistant/catalog')).runtime.available, true)
const person = await call('admin', 'GET', '/auth/me')
const project = await call('admin', 'POST', '/operations', { name: `跨标签同步验收 ${Date.now().toString(36)}`, owner_id: person.id })
const task = await call('admin', 'POST', `/operations/${project.id}/tasks`, { title: '验收设备清单', owner_id: person.id, due_date: '2026-10-25' })
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' })
const errors = []
let chat
try {
  const watcher = await loginPage(browser, 'admin')
  watcher.on('pageerror', error => errors.push(error.message))
  await watcher.goto(`${WEB}/operations/${project.id}`)
  const overview = watcher.getByRole('region', { name: '2026-10 月度汇总' })
  await overview.getByText('0%', { exact: true }).waitFor()
  await watcher.evaluate(() => {
    window.businessSyncEvents = 0
    window.addEventListener('storage', event => { if (event.key === 'jc-business-revision') window.businessSyncEvents++ })
  })

  chat = await watcher.context().newPage()
  chat.on('pageerror', error => errors.push(error.message))
  let sent = 0
  chat.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/assistant/tasks') sent++ })
  await chat.goto(`${WEB}/operations/${project.id}?task=${task.id}`)
  await chat.getByRole('dialog', { name: '任务详情' }).getByRole('button', { name: '用小聚办理', exact: true }).click()
  const input = chat.getByRole('textbox', { name: '向小聚描述需求' })
  await chat.waitForFunction(id => document.querySelector('textarea[aria-label="向小聚描述需求"]')?.value.includes(`任务编号 ${id}`), task.id)
  await chat.getByRole('button', { name: '能办什么', exact: true }).click()
  const menu = chat.getByRole('dialog', { name: '小聚能办什么' })
  await menu.getByRole('textbox', { name: '搜索可办事项' }).fill('提交任务进展反馈')
  await menu.getByRole('button', { name: /提交任务进展反馈.*需确认/ }).click()
  assert.equal(sent, 0, '选择事项或从详情打开小聚只填草稿，不自动执行')
  await chat.getByRole('button', { name: '选择模型与思考深度' }).click()
  await chat.getByRole('menuitem', { name: /快速/ }).click()
  await input.fill(`为运营项目「${project.name}」中的任务编号 ${task.id}「${task.title}」提交进展反馈：校方已确认设备清单，任务已完成，进度100%。`)
  await chat.getByRole('button', { name: '发送需求' }).click()
  // The provisional streaming card remounts when the reply settles.
  const reply = chat.getByRole('article', { name: '小聚的回复', exact: true })
  const action = reply.getByLabel('业务操作确认单').filter({ hasText: '提交任务进展反馈' }).first()
  const toggle = action.getByRole('button', { name: /提交任务进展反馈/ })
  const confirm = action.getByRole('button', { name: '确认执行', exact: true })
  await toggle.waitFor({ timeout: 100000 })
  assert.equal(await toggle.getAttribute('aria-expanded'), 'false')
  assert.equal(await confirm.count(), 0)
  await chat.screenshot({ path: path.join(OUT, 'screens', 'xiaoju-action-folded.png'), animations: 'disabled' })
  await toggle.click()
  await confirm.waitFor()
  await toggle.click()
  await confirm.waitFor({ state: 'hidden' })
  await toggle.click()
  assert.equal((await call('admin', 'GET', `/operations/tasks/${task.id}`)).status, 'todo')
  await confirm.click()
  await watcher.waitForFunction(() => window.businessSyncEvents > 0, undefined, { timeout: 12000 })
  await overview.getByText('100%', { exact: true }).waitFor({ timeout: 10000 })
  const result = await call('admin', 'GET', `/operations/tasks/${task.id}`)
  assert.equal(result.status, 'done')
  assert.match(result.updates[0].content, /设备清单/)
  assert.deepEqual(errors, [])
  await watcher.screenshot({ path: path.join(OUT, 'screens', 'monthly-after-xiaoju.png'), animations: 'disabled' })
  console.log('PASS 小聚真实执行单默认收起，可展开核对与确认；确认后另一标签的月度图表立即更新')
} catch (error) {
  if (chat) await chat.screenshot({ path: path.join(OUT, 'screens', 'xiaoju-sync-failure.png'), animations: 'disabled' })
  throw error
} finally {
  await browser.close()
}
