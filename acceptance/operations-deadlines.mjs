// Only synthetic tasks on a local test server. Exercise the existing task dialog and monthly view.
import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, WEB, OUT, call, loginPage } from './lib.mjs'

assert.equal(new URL(API).hostname, '127.0.0.1')
for (const base of [API, WEB]) assert.equal((await fetch(base + '/health').then(r => r.json())).env, 'test')

const manager = await call('wujun', 'GET', '/auth/me')
const member = await call('xiekai', 'GET', '/auth/me')
const project = await call('wujun', 'POST', '/operations', { name: '任务按期验收 ' + Date.now().toString(36), owner_id: manager.id })
await call('wujun', 'POST', `/operations/${project.id}/members`, { user_id: member.id, role: 'member' })
const newTask = (title, due_date, owner_id = member.id) => call('wujun', 'POST', `/operations/${project.id}/tasks`, { title, due_date, owner_id })
const late = await newTask('提交校方核对清单', '2026-10-19')
await call('wujun', 'PUT', `/operations/tasks/${late.id}`, { due_date: '2026-10-25' })
const onTime = await newTask('当天提交验收照片', '2026-10-20')
await call('xiekai', 'POST', `/operations/tasks/${onTime.id}/feedback`, { status: 'done', content: '照片已归档' })
await newTask('下周确认设备', '2026-10-28', manager.id)

const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' })
const errors = []
let worker, boss
try {
  const taskUrl = `${WEB}/operations/${project.id}?task=${late.id}`
  worker = await loginPage(browser, 'xiekai')
  worker.on('pageerror', error => errors.push(error.message))
  await worker.goto(taskUrl)
  const workerTask = worker.getByRole('dialog', { name: '任务详情', exact: true })
  await workerTask.getByRole('heading', { name: late.title, exact: true }).waitFor()
  await workerTask.getByRole('radiogroup', { name: '完成情况', exact: true }).getByText('已完成', { exact: true }).click()
  await workerTask.getByLabel('完成结果', { exact: true }).fill('校方已签字，清单已交接')
  await workerTask.getByLabel('延期原因（可选）', { exact: true }).fill('等待校方签字')
  await workerTask.getByRole('button', { name: '提交进展', exact: true }).click()
  const workerDelay = workerTask.getByRole('region', { name: '延期说明', exact: true })
  await workerDelay.getByText('待认可', { exact: true }).waitFor()
  assert.equal(await workerDelay.getByRole('button', { name: '认可', exact: true }).count(), 0)

  boss = await loginPage(browser, 'zhangzhenhua')
  boss.on('pageerror', error => errors.push(error.message))
  await boss.goto(`${WEB}/operations`)
  await boss.getByRole('tab', { name: '当前需要做什么', exact: true }).click()
  await boss.getByText('待我复核延期', { exact: true }).click()
  await boss.getByRole('row', { name: /提交校方核对清单/ }).getByRole('button', { name: '复核延期', exact: true }).waitFor()
  await boss.goto(`${WEB}/operations/${project.id}`)
  const overview = boss.getByRole('region', { name: '2026-10 月度汇总' })
  await overview.getByText('67%', { exact: true }).waitFor()
  for (const label of ['按时完成 1 项', '逾期完成 1 项', '未完成 1 项']) await overview.getByText(label, { exact: true }).waitFor()
  await boss.getByText(/按负责人查看/).click()
  const ownerTable = boss.getByRole('table', { name: '负责人任务统计', exact: true })
  const owner = ownerTable.getByRole('row').filter({ hasText: member.name })
  assert.deepEqual(await owner.getByRole('cell').allTextContents(), ['2', '2', '1', '1', '0'])
  await boss.screenshot({ path: path.join(OUT, 'screens', 'operations-deadlines-monthly.png'), animations: 'disabled' })

  await boss.getByRole('button', { name: '筛选', exact: true }).click()
  await boss.getByText('待我复核延期', { exact: true }).click()
  await boss.getByRole('button', { name: '复核延期', exact: true }).click()
  const bossDelay = boss.getByRole('dialog', { name: '任务详情', exact: true }).getByRole('region', { name: '延期说明', exact: true })
  await bossDelay.getByText('等待校方签字', { exact: true }).waitFor()
  await bossDelay.getByRole('textbox', { name: '延期复核意见', exact: true }).fill('请补充实际签字日期')
  await bossDelay.getByRole('button', { name: '不认可', exact: true }).click()
  await bossDelay.getByText('未认可', { exact: true }).first().waitFor()
  const before = await call('zhangzhenhua', 'GET', `/operations/tasks/${late.id}`)

  await worker.goto(taskUrl)
  await workerDelay.getByRole('button', { name: '更新延期原因', exact: true }).click()
  await workerDelay.getByRole('textbox', { name: '延期原因', exact: true }).fill('校方于 10 月 20 日补签')
  await workerDelay.getByRole('button', { name: '提交延期原因', exact: true }).click()
  await workerDelay.getByText('待认可', { exact: true }).waitFor()
  await boss.goto(taskUrl)
  await bossDelay.getByText('校方于 10 月 20 日补签', { exact: true }).waitFor()
  await bossDelay.getByRole('button', { name: '认可', exact: true }).click()
  await bossDelay.getByText('已认可', { exact: true }).first().waitFor()
  const after = await call('zhangzhenhua', 'GET', `/operations/tasks/${late.id}`)
  assert.equal(after.deadline_status, 'late_done')
  assert.equal(after.completed_at, before.completed_at)
  assert.equal(after.deadline_basis, '2026-10-19')
  assert.equal(after.delay_history[1].decision, 'rejected')

  const mobile = await loginPage(browser, 'zhangzhenhua', { width: 390, height: 844 })
  await mobile.goto(`${WEB}/operations/${project.id}`)
  await mobile.getByText(/按负责人查看/).click()
  await mobile.getByRole('table', { name: '负责人任务统计', exact: true }).waitFor()
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await mobile.screenshot({ path: path.join(OUT, 'screens', 'operations-deadlines-mobile.png'), animations: 'disabled' })
  assert.deepEqual(errors, [])
  console.log('PASS: reason filed with feedback, monthly by owner, reject and resubmit, accept without altering late completion, mobile layout.')
} catch (error) {
  await (boss || worker)?.screenshot({ path: path.join(OUT, 'screens', 'operations-deadlines-failure.png'), animations: 'disabled' })
  throw error
} finally {
  await browser.close()
}
