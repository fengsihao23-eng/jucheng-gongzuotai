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
const admin = await loginPage(browser, 'admin', { width: 1440, height: 960 })
const worker = await loginPage(browser, 'yangyue', { width: 390, height: 844 })
const observer = await loginPage(browser, 'chenjing')
for (const page of [admin, worker, observer]) page.setDefaultTimeout(12000)
const workerProfile = await call('yangyue', 'GET', '/auth/me')
const observerProfile = await call('chenjing', 'GET', '/auth/me')
const name = '校园交付协同验收'
const work = '确认试点学校交付清单'
const newTask = '复核项目资料并归档'
const feedback = '已与校方确认设备清单。'
const button = (root, label) => root.getByRole('button', { name: label, exact: true })
const taskDialog = page => page.getByRole('dialog', { name: '任务情况', exact: true })
const taskCard = (page, title) => page.locator('.task-card').filter({ hasText: title })
const teamTab = page => page.getByRole('tab', { name: '项目团队', exact: true })
const selectBox = (page, label) => page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: label, exact: true }) })
let projectUrl

async function fillDate(root, name, date) {
  const field = root.getByRole('combobox', { name, exact: true })
  await field.fill(date)
  await field.press('Enter')
}

async function capture(page, name) {
  await page.locator('.el-message').first().waitFor({ state: 'hidden' })
  const file = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, file), animations: 'disabled' })
  return file
}

async function notice(title, recipient, suffix) {
  for (let attempt = 0; attempt < 12; attempt++) {
    const sent = await call('admin', 'GET', '/dev/mock/outbox', { limit: 200 })
    if (sent.some(row => row.type === 'textcard' && row.title === title && row.to.includes(recipient) && row.url?.endsWith(suffix))) return
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  throw new Error(`Missing WeCom card for ${recipient}: ${title}`)
}

try {
  await step('TEAM-01', '新建重点项目时选团队成员，可直接将任务交给该成员', async () => {
    assert.ok(!workerProfile.menus.includes('market') && !workerProfile.menus.includes('projects'))
    await admin.goto(WEB + '/market')
    await button(admin, '新建重点项目').click()
    const form = admin.getByRole('dialog', { name: '新建重点项目', exact: true })
    await form.getByLabel('重点项目名称').fill(name)
    await fillDate(form, '项目准备日期', '2026-11-26')
    await fillDate(form, '项目完成日期', '2026-12-10')
    await selectBox(admin, '新建项目团队成员').click()
    await admin.getByRole('option').filter({ hasText: workerProfile.name }).click()
    await form.getByRole('heading', { name: '项目任务', exact: true }).click()
    await button(form, '添加任务').click()
    const fields = form.getByRole('region', { name: '任务 1', exact: true })
    await fields.getByLabel('重点任务名称').fill(work)
    await fields.getByRole('combobox', { name: '重点任务负责人', exact: true }).click()
    await admin.getByRole('option', { name: workerProfile.name, exact: true }).click()
    await button(form, '创建重点项目').click()
    await button(admin, `查看重点项目：${name}`).click()
    await taskCard(admin, work).waitFor()
    projectUrl = new URL(admin.url()).pathname
    const created = await call('admin', 'GET', projectUrl)
    assert.equal(created.tasks[0].owner_id, workerProfile.id)
    await notice('你已加入重点项目团队', 'yangyue', projectUrl)
    await notice('收到重点项目任务', 'yangyue', `${projectUrl}?task=${created.tasks[0].id}`)

    await worker.goto(WEB + '/market')
    await worker.getByRole('link', { name: '重点项目', exact: true, includeHidden: true }).waitFor({ state: 'attached' })
    await button(worker, `查看重点项目：${name}`).click()
    await taskCard(worker, work).locator('.action').getByText(/^反馈进展/).waitFor()
    assert.equal(await worker.getByRole('tab', { name: '开票回款', exact: true }).count(), 0)
    assert.equal(await button(worker, '安排任务').count(), 0)
    assert.ok(await worker.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    return capture(worker, '02-成员手机查看任务')
  })

  await step('TEAM-02', '项目团队可添加同事，任务发布及成员进展随企微卡片送达', async () => {
    await teamTab(admin).click()
    const pane = admin.getByRole('tabpanel', { name: '项目团队', exact: true })
    await selectBox(admin, '选择重点项目成员').click()
    await admin.getByRole('option').filter({ hasText: observerProfile.name }).click()
    await button(pane, '加入团队').click()
    await pane.getByRole('row').filter({ hasText: observerProfile.name }).getByText('团队成员', { exact: true }).waitFor()
    const evidence = await capture(admin, '01-重点项目团队')
    await notice('你已加入重点项目团队', 'chenjing', projectUrl)

    await button(admin, '安排任务').click()
    const form = admin.getByRole('dialog', { name: '安排任务', exact: true })
    await form.getByLabel('重点任务名称').fill(newTask)
    await button(form, '保存安排').click()
    await taskDialog(admin).getByRole('heading', { name: newTask, exact: true }).waitFor()
    await button(taskDialog(admin), '关闭').click()
    const tasks = (await call('admin', 'GET', projectUrl)).tasks
    const added = tasks.find(item => item.title === newTask)
    const original = tasks.find(item => item.title === work)
    await notice('重点项目新增任务', 'chenjing', `${projectUrl}?task=${added.id}`)
    await worker.goto(WEB + `${projectUrl}?task=${original.id}`)
    await taskDialog(worker).getByLabel('重点任务进展').fill(feedback)
    await button(taskDialog(worker), '提交进展').click()
    await worker.getByText('进展已保存', { exact: true }).waitFor()
    await notice('重点项目有新进展', 'chenjing', `${projectUrl}?task=${original.id}`)

    await observer.goto(WEB + `${projectUrl}?task=${original.id}`)
    const dialog = taskDialog(observer)
    await dialog.getByRole('heading', { name: work, exact: true }).waitFor()
    assert.equal(await button(dialog, '提交进展').count(), 0)
    await button(dialog, '过程记录').click()
    await observer.getByRole('dialog', { name: '过程记录', exact: true }).getByText(feedback, { exact: false }).first().waitFor()
    return evidence
  })

  await step('TEAM-03', '移除成员后取消访问，原任务负责人的访问继续可用', async () => {
    const pane = admin.getByRole('tabpanel', { name: '项目团队', exact: true })
    await teamTab(admin).click()
    await button(pane, `移除成员 ${observerProfile.name}`).click()
    const dialog = admin.getByRole('dialog', { name: '移除团队成员', exact: true })
    await dialog.getByLabel('删除密码').fill(process.env.DELETE_PASSWORD || 'admin123')
    await button(dialog, '确认移除').click()
    await pane.getByRole('row').filter({ hasText: observerProfile.name }).waitFor({ state: 'hidden' })
    await observer.goto(WEB + projectUrl)
    await observer.waitForURL(/\/403$/)
    assert.equal((await call('chenjing', 'GET', projectUrl, null, { raw: true })).status, 403)
    assert.equal((await call('yangyue', 'GET', projectUrl)).name, name)
  })
} finally {
  await browser.close()
  process.exitCode = save().failed ? 1 : 0
}
