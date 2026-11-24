import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, call, loginPage, save, shot, step } from './lib.mjs'

for (const url of [API, WEB]) {
  assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname), 'Use an isolated local test service')
}
const health = await fetch(API + '/health').then(r => r.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')

// Run against a seeded local database with the mock workbook, a test ken account, and an AI test response.
const users = (await call('admin', 'GET', '/admin/users', { page_size: 100 })).items
const owner = users.find(u => u.userid === 'wujun')
const employee = users.find(u => u.userid === 'xiekai')
const adminUser = users.find(u => u.userid === 'admin')
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const admin = await loginPage(browser, 'admin', { width: 1440, height: 1080 })
const ownerPage = await loginPage(browser, 'wujun')
const ken = await loginPage(browser, 'ken')
const member = await loginPage(browser, 'xiekai')
const mobile = await loginPage(browser, 'admin', { width: 390, height: 844 })
for (const page of [admin, ownerPage, ken, member, mobile]) page.setDefaultTimeout(12000)

const keyName = '校园服务升级 · 验收示例'
const collectionName = '校园网络维护尾款 · 验收示例'
const progress = '已向校方提交验收资料，并核实待付款流程。'
const blocker = '校方财务正在复核验收单。'
const nextStep = '周五与校方财务确认付款日期。'
const taskTitle = '零星回款任务详情'
let projectCode

async function setDate(dialog, label, value) {
  const field = dialog.getByRole('combobox', { name: label, exact: true })
  await field.fill(value)
  await field.press('Enter')
}

async function capture(page, name, dialog) {
  if (dialog) await dialog.locator('.el-dialog__body').evaluate(body => { body.scrollTop = 0 })
  await page.locator('.el-message').first().waitFor({ state: 'hidden' })
  await page.waitForFunction(() => document.getAnimations().every(animation =>
    animation.playState !== 'running' || animation.effect?.getTiming().iterations === Infinity))
  await shot(page, name)
}

await step('PROJECT-01', '新建重点项目后按所选负责人发送带项目链接的企微提醒', async () => {
  await admin.goto(WEB + '/market')
  await admin.getByRole('button', { name: '新建重点项目', exact: true }).click()
  const dialog = admin.getByRole('dialog', { name: '新建重点项目', exact: true })
  await dialog.getByRole('textbox', { name: '重点项目名称', exact: true }).fill(keyName)
  await dialog.getByRole('textbox', { name: '重点项目内容', exact: true }).fill('核对现有设备清单，安排升级方案评审。')
  await dialog.getByRole('combobox', { name: '重点项目负责人', exact: true }).click()
  await admin.getByRole('option', { name: owner.name, exact: true }).click()
  await setDate(dialog, '项目完成日期', '2026-12-10')
  await dialog.getByRole('button', { name: '创建重点项目', exact: true }).click()
  await admin.waitForURL(/\/market\/projects\/JC-/)
  projectCode = new URL(admin.url()).pathname.split('/').pop()
  const messages = await call('admin', 'GET', '/dev/mock/outbox')
  const routed = messages.find(msg => msg.url?.endsWith('/market/projects/' + projectCode))
  assert.deepEqual(routed?.to, ['wujun'])
  assert.equal(routed.type, 'textcard')

  await ownerPage.goto(WEB + '/notifications')
  const row = ownerPage.getByRole('row').filter({ hasText: keyName })
  await row.getByText('你有新的重点项目', { exact: true }).waitFor()
  await capture(ownerPage, '01-assignment-notice')
  await row.click()
  await ownerPage.getByRole('heading', { name: keyName, exact: true }).waitFor()
})

await step('PROJECT-02', '负责人无暂停入口，指定账号取消与确认后状态一致', async () => {
  const path = '/market/projects/' + projectCode
  assert.equal(await ownerPage.getByRole('button', { name: '暂停跟进', exact: true }).count(), 0)
  assert.equal((await call('wujun', 'PUT', path + '/pause', { paused: true }, { raw: true })).status, 403)
  await ken.goto(WEB + path)
  const pause = ken.getByRole('button', { name: '暂停跟进', exact: true })
  await pause.click()
  await ken.getByRole('button', { name: '确认暂停', exact: true }).waitFor()
  await capture(ken, '02-pause-confirmation')
  await ken.getByRole('button', { name: '取消', exact: true }).click()
  assert.equal((await call('ken', 'GET', path)).paused, false)
  await pause.click()
  await ken.getByRole('button', { name: '确认暂停', exact: true }).click()
  await ken.getByRole('button', { name: '恢复跟进', exact: true }).waitFor()
  assert.equal((await call('ken', 'GET', path)).paused, true)
  await ken.getByRole('button', { name: '恢复跟进', exact: true }).click()
  await pause.waitFor()
  assert.equal((await call('ken', 'GET', path)).paused, false)
})

await step('COLLECTION-01', '登记零星项目并提交任务反馈，列表和过程记录同步显示', async () => {
  await call('admin', 'POST', '/collections/sporadic', {
    name: '演示教学终端零星维保', customer: '演示实验学校', owner_id: adminUser.id,
    expected_amount: '3600.00', due_on: '2026-12-05',
  })
  await admin.goto(WEB + '/collections')
  await admin.getByRole('button', { name: '零星回款', exact: true }).click()
  await admin.getByRole('button', { name: '登记零星回款', exact: true }).click()
  const register = admin.getByRole('dialog', { name: '登记零星回款项目', exact: true })
  await register.getByRole('textbox', { name: '项目名称', exact: true }).fill(collectionName)
  await register.getByRole('textbox', { name: '客户', exact: true }).fill('演示第一中学')
  await register.getByRole('combobox', { name: '负责人', exact: true }).click()
  await admin.getByRole('option', { name: new RegExp('^' + adminUser.name) }).click()
  await register.getByRole('textbox', { name: '预计回款金额', exact: true }).fill('12800.50')
  await setDate(register, '截止日期', '2026-11-30')
  await register.getByRole('textbox', { name: '跟进要求', exact: true }).fill('核对校方验收资料，确认尾款付款节点。')
  await register.getByRole('button', { name: '保存项目', exact: true }).click()
  const task = admin.getByRole('dialog', { name: taskTitle, exact: true })
  await task.getByRole('heading', { name: '任务反馈', exact: true }).waitFor()
  await task.getByRole('textbox', { name: '目前进度情况', exact: true }).fill(progress)
  await task.getByText('遇到阻碍', { exact: true }).click()
  await task.getByRole('textbox', { name: '未完成原因', exact: true }).fill(blocker)
  await task.getByRole('textbox', { name: '下一步安排', exact: true }).fill(nextStep)
  await capture(admin, '03-sporadic-task-feedback', task)
  await task.getByRole('button', { name: '提交进展', exact: true }).click()
  await admin.getByText('进展已提交', { exact: true }).waitFor()
  await task.getByRole('button', { name: '过程记录', exact: true }).click()
  const history = admin.getByRole('dialog', { name: '过程记录', exact: true })
  await history.getByText(progress, { exact: true }).waitFor()
  await admin.keyboard.press('Escape')
  await history.waitFor({ state: 'hidden' })
  await task.getByRole('button', { name: '关闭', exact: true }).click()
  const row = admin.getByRole('row').filter({ hasText: collectionName })
  await row.getByText('遇到阻碍', { exact: true }).waitFor()
  await capture(admin, '04-sporadic-project-list')
})

await step('COLLECTION-02', '固定按钮一键生成带台账金额、风险和下一步的 AI 模板报告', async () => {
  await admin.goto(WEB + '/collections')
  await admin.getByRole('button', { name: '回款总结', exact: true }).click()
  const report = admin.getByRole('dialog', { name: '回款总结', exact: true })
  await report.getByText('AI 已整理项目反馈', { exact: true }).waitFor()
  const text = await report.locator('pre').textContent()
  for (const expected of ['25,000.00 元', '一、回款概况', '二、项目跟进进展', '三、风险与待协调事项',
    '四、下一步安排', collectionName, blocker, nextStep]) assert.ok(text.includes(expected), expected)
  await capture(admin, '05-collection-summary')
})

await step('OPERATIONS-01', '普通团队成员添加和编辑对手信息，刷新后仍可查看', async () => {
  const project = await call('admin', 'POST', '/operations', {
    name: '校园数字化运营 · 验收示例', category: '智慧校园', owner_id: adminUser.id, enabled_sections: ['progress'],
  })
  const path = '/operations/' + project.id
  await call('admin', 'POST', path + '/members', { user_id: employee.id, role: 'member' })
  await member.goto(WEB + path)
  const tab = member.getByRole('tab', { name: '对手信息', exact: true })
  await tab.click()
  await member.getByRole('button', { name: '添加对手', exact: true }).click()
  const add = member.getByRole('dialog', { name: '添加对手信息', exact: true })
  await add.getByRole('textbox', { name: '对手名称', exact: true }).fill('示例教育服务商')
  await add.getByRole('textbox', { name: '对手情况', exact: true }).fill('计划提交校园设备更新方案，提出分批部署。')
  await add.getByRole('textbox', { name: '应对计划', exact: true }).fill('整理已有案例。')
  await add.getByRole('button', { name: '保存', exact: true }).click()
  await member.getByRole('button', { name: '编辑对手 示例教育服务商', exact: true }).click()
  const edit = member.getByRole('dialog', { name: '编辑对手信息', exact: true })
  const response = '更新交付周期对照表，准备下次评审。'
  await edit.getByRole('textbox', { name: '应对计划', exact: true }).fill(response)
  await edit.getByRole('button', { name: '保存', exact: true }).click()
  await member.getByText(response, { exact: true }).waitFor()
  await member.reload()
  await tab.click()
  await member.getByText(response, { exact: true }).waitFor()
  await capture(member, '06-operations-competitor')
})

await step('MOBILE-01', '390px 手机可打开回款项目和回款总结', async () => {
  await mobile.goto(WEB + '/collections')
  await mobile.getByRole('button', { name: '回款总结', exact: true }).click()
  const report = mobile.getByRole('dialog', { name: '回款总结', exact: true })
  await report.locator('pre').waitFor()
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await report.getByRole('button', { name: '关闭', exact: true }).click()
  await mobile.getByRole('button', { name: '零星回款', exact: true }).click()
  await mobile.getByRole('button', { name: collectionName, exact: true }).click()
  await mobile.getByRole('dialog', { name: taskTitle, exact: true }).getByRole('heading', { name: '任务反馈' }).waitFor()
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
})

await browser.close()
const result = save()
if (result.failed) process.exitCode = 1
