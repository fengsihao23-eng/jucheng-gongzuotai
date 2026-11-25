import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, OUT, WEB, call, loginPage, save, step } from './lib.mjs'

for (const url of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname))
const health = await fetch(`${API}/health`).then(response => response.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')
assert.equal(health.overrides, '')

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const page = await loginPage(browser, 'admin', { width: 1500, height: 1030 })
const admin = await call('admin', 'GET', '/auth/me')
const operator = await call('xiekai', 'GET', '/auth/me')
const keyOwner = await call('xuhao', 'GET', '/auth/me')
const soon = '2026-11-28 16:30'
const passed = '2026-11-24T16:00:00+08:00'
const statuses = page.getByRole('group', { name: '按任务状态筛选', exact: true })
const card = title => page.locator('.task-card').filter({ hasText: title })
const notices = []
page.setDefaultTimeout(9000)

async function fillDate(control, value) {
  await control.fill(value)
  await control.press('Enter')
}
async function capture(name) {
  await page.locator('.el-message').first().waitFor({ state: 'hidden' })
  const file = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, file), animations: 'disabled', fullPage: true })
  return file
}
async function closeDialog(dialog) {
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'hidden' })
}
async function checkFilters(future, overdue) {
  await statuses.getByRole('button', { name: /^待反馈/ }).click()
  await card(future).getByText(`反馈截止 ${soon}`, { exact: true }).waitFor()
  assert.equal(await card(overdue).count(), 0)
  await statuses.getByRole('button', { name: /^逾期未反馈/ }).click()
  await card(overdue).getByText('逾期未反馈', { exact: true }).waitFor()
  assert.equal(await card(future).count(), 0)
}

try {
  const ops = await call('admin', 'POST', '/operations', { name: '智慧校园巡检', owner_id: admin.id })
  const operationPath = `/operations/${ops.id}`
  await call('admin', 'POST', `${operationPath}/members`, { user_id: operator.id, role: 'member' })
  const key = await call('admin', 'POST', '/market/projects', { name: '校园设备交付', owner_id: admin.id,
    preparation_on: '2026-11-20', due_on: '2026-12-10' })
  const keyPath = `/market/projects/${key.project_code}`

  await step('FEEDBACK-01', '运营任务可设置反馈时间并在运营项目筛选逾期未反馈', async () => {
    const future = '登记首轮学校巡检结果'
    const overdue = await call('admin', 'POST', `${operationPath}/tasks`, { title: '反馈教室网络初检结果', owner_id: operator.id,
      cadence: 'node', due_date: '2026-12-05', feedback_due_at: passed })
    await page.goto(WEB + operationPath)
    await page.getByRole('button', { name: '布置任务', exact: true }).click()
    const form = page.getByRole('dialog', { name: '布置重点任务', exact: true })
    await form.getByLabel('要完成什么', { exact: true }).fill(future)
    await form.getByLabel('负责人', { exact: true }).click()
    await page.getByRole('option', { name: operator.name, exact: true }).click()
    await fillDate(form.getByLabel('截止日期', { exact: true }), '2026-12-05')
    await fillDate(form.getByRole('combobox', { name: '反馈截止时间', exact: true }), soon)
    const screenshot = await capture('01-运营任务反馈时间')
    await form.getByRole('button', { name: '发布任务', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '任务详情', exact: true })
    await dialog.getByText(`反馈截止 ${soon}`, { exact: true }).waitFor()
    await closeDialog(dialog)
    const task = (await call('admin', 'GET', operationPath)).tasks.find(item => item.title === future)
    assert.equal(Date.parse(task.feedback_due_at), Date.parse('2026-11-28T16:30:00+08:00'))
    notices.push({ pagePath: operationPath, detail: `/operations/tasks/${overdue.id}`, title: overdue.title, recipient: 'xiekai',
      link: `${operationPath}?task=${overdue.id}`, assignment: '收到运营项目任务', reminder: '运营任务逾期未反馈', dialog: '任务详情' })
    await checkFilters(future, overdue.title)
    await capture('02-运营项目逾期未反馈')
    return screenshot
  })

  await step('FEEDBACK-02', '重点项目保存反馈时间，区分任务完成日期和逾期未反馈', async () => {
    const future = '提交校方设备验收纪要'
    const overdue = await call('admin', 'POST', `${keyPath}/tasks`, { title: '反馈设备进场核对结果', owner_id: keyOwner.id,
      cadence: 'node', preparation_on: '2026-11-20', due_on: '2026-12-05', feedback_due_at: passed })
    await page.goto(WEB + keyPath)
    await page.getByRole('button', { name: '安排任务', exact: true }).click()
    const form = page.getByRole('dialog', { name: '安排任务', exact: true })
    await form.getByRole('textbox', { name: '重点任务名称', exact: true }).fill(future)
    await form.getByRole('combobox', { name: '重点任务负责人', exact: true }).click()
    await page.getByRole('option', { name: keyOwner.name, exact: true }).click()
    await fillDate(form.getByRole('combobox', { name: '任务完成日期', exact: true }), '2026-12-05')
    await fillDate(form.getByRole('combobox', { name: '反馈截止时间', exact: true }), soon)
    await form.getByRole('button', { name: '保存安排', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '任务情况', exact: true })
    await dialog.getByText(`反馈截止 ${soon}`, { exact: true }).waitFor()
    await closeDialog(dialog)
    const task = (await call('admin', 'GET', keyPath)).tasks.find(item => item.title === future)
    assert.equal(Date.parse(task.feedback_due_at), Date.parse('2026-11-28T16:30:00+08:00'))
    notices.push({ pagePath: keyPath, detail: `${keyPath}/tasks/${overdue.id}`, title: overdue.title, recipient: 'xuhao',
      link: `${keyPath}?task=${overdue.id}`, assignment: '收到重点项目任务', reminder: '重点项目任务逾期未反馈', dialog: '任务情况' })
    await checkFilters(future, overdue.title)
    const screenshot = await capture('03-重点项目逾期未反馈')
    await page.goto(WEB + '/market')
    await page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '选择重点项目' }) }).click()
    await page.getByRole('option', { name: '校园设备交付', exact: true }).click()
    await checkFilters(future, overdue.title)
    return screenshot
  })

  await step('FEEDBACK-03', '发给本人的是企微文字卡片，重复扫描与提交反馈后均无重复提醒', async () => {
    assert.equal(notices.length, 2)
    const job = () => call('admin', 'POST', '/admin/jobs/task_feedback_deadline_reminders/run', { force: true })
    for (let i = 0; i < 2; i++) assert.equal((await job()).status, 'success')
    const outbox = () => call('admin', 'GET', '/dev/mock/outbox', { limit: 200 })
    const matching = (rows, item, title) => rows.filter(row => row.title === title && (row.url || '').endsWith(item.link))
    const rows = await outbox()
    let evidence
    for (const item of notices) {
      for (const title of [item.assignment, item.reminder]) {
        const sent = matching(rows, item, title)
        assert.equal(sent.length, 1)
        assert.deepEqual(sent[0].to, [item.recipient])
        assert.equal(sent[0].type, 'textcard')
      }
      assert.ok(matching(rows, item, item.reminder)[0].content.includes('2026-11-24 16:00'))
      await page.goto(WEB + item.link)
      const dialog = page.getByRole('dialog', { name: item.dialog, exact: true })
      await dialog.getByRole('button', { name: '过程记录', exact: true }).click()
      const history = page.getByRole('dialog', { name: '过程记录', exact: true })
      await history.getByText('企业微信提醒投递情况', { exact: true }).click()
      await history.getByText(new RegExp(item.reminder)).waitFor()
      if (!evidence) evidence = await capture('04-企业微信提醒投递验证')
      const task = await call(item.recipient, 'GET', item.detail)
      const feedback = await call(item.recipient, 'POST', item.detail + '/feedback', {
        status: item.recipient === 'xuhao' ? 2 : 'doing', content: '已确认情况并提交本次进展。', expected_revision: task.revision,
      })
      assert.equal(feedback.feedback_status, null)
      await page.goto(WEB + item.pagePath)
      await statuses.getByRole('button', { name: /^逾期未反馈/ }).click()
      assert.equal(await card(item.title).count(), 0)
    }
    assert.equal((await job()).status, 'success')
    const after = await outbox()
    for (const item of notices) assert.equal(matching(after, item, item.reminder).length, 1)
    return evidence
  })
} finally {
  await browser.close()
  process.exitCode = save().failed ? 1 : 0
}
