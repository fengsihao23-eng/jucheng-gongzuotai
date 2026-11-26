import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, OUT, call, loginPage, save, shot, step as recordStep } from './lib.mjs'

const health = await fetch(API + '/health').then(r => r.json())
if (health.env !== 'test' || !['localhost', '127.0.0.1'].includes(new URL(API).hostname)) throw new Error('Only an isolated local test service is allowed')
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const users = (await call('admin', 'GET', '/admin/users', { page_size: 100 })).items
const person = name => users.find(u => u.userid === name)
const p = await call('admin', 'POST', '/operations', { name: '会议完整闭环验收 ' + Date.now(), owner_id: person('wujun').id })
for (const [user, role] of [['xiekai', 'member'], ['xuhao', 'member'], ['yangyue', 'viewer']]) await call('admin', 'POST', `/operations/${p.id}/members`, { user_id: person(user).id, role })
const school = await call('admin', 'POST', `/operations/${p.id}/schools`, { name: '会议试点学校' })
const task = await call('admin', 'POST', `/operations/${p.id}/tasks`, {
  title: '学校接口对接', owner_id: person('xiekai').id, due_date: '2026-10-30', school_id: school.id, kind: 'outreach',
  nodes: [{ name: '确认需求', due_date: '2026-10-25' }, { name: '完成联调', due_date: '2026-10-30' }],
})
const manager = await loginPage(browser, 'wujun')
const employee = await loginPage(browser, 'xiekai', { width: 390, height: 844 })
const wait = locator => locator.waitFor({ state: 'visible', timeout: 12000 })
const taskUrl = `${WEB}/operations/${p.id}?task=${task.id}`
async function selectBox(scope, name) {
  await scope.getByRole('combobox', { name, exact: true }).locator('xpath=ancestor::div[contains(@class,"el-select__wrapper")]').click()
}
async function step(id, title, fn) {
  await recordStep(id, title, async () => {
    try { return await fn() }
    catch (error) { await shot(manager, id + '-manager-failure').catch(() => {}); await shot(employee, id + '-employee-failure').catch(() => {}); throw error }
  })
  save()
}

await step('ITER-01', '管理者调整周期并保留原任务', async () => {
  await manager.goto(taskUrl)
  await manager.getByRole('button', { name: '调整安排' }).click()
  const form = manager.getByRole('dialog', { name: '调整任务安排' })
  await selectBox(form, '反馈频率')
  await manager.getByRole('option', { name: '每周反馈', exact: true }).click()
  await form.getByRole('button', { name: '保存安排', exact: true }).click()
  await form.waitFor({ state: 'hidden' })
  assert.equal((await call('wujun', 'GET', `/operations/tasks/${task.id}`)).cadence, 'weekly')
})

await step('ITER-02', '管理者提出定向要求，员工待办可见', async () => {
  await manager.goto(taskUrl)
  const dlg = manager.getByRole('dialog', { name: '任务详情' })
  await dlg.getByText('提出要求', { exact: true }).click()
  await selectBox(dlg, '讨论接收人')
  await manager.getByRole('option', { name: person('xiekai').name, exact: true }).click()
  await dlg.getByRole('textbox', { name: '要求或留言', exact: true }).fill('请先确认学校接口清单，并回复安排。')
  await dlg.getByRole('button', { name: '发送讨论', exact: true }).click()
  await wait(dlg.getByText('待回复', { exact: true }))
  const row = await call('xiekai', 'GET', `/operations/tasks/${task.id}`)
  assert.equal(row.last_feedback_at, null)
  assert.equal(row.discussions.length, 1)
})

await step('ITER-03', '手机端本人回复与节点进展分开保存', async () => {
  await employee.goto(taskUrl)
  const dlg = employee.getByRole('dialog', { name: '任务详情' })
  await dlg.getByRole('button', { name: '回复这条要求' }).click()
  await dlg.getByRole('textbox', { name: '回复内容', exact: true }).fill('收到，今天向学校核对。')
  await dlg.getByRole('button', { name: '提交回复', exact: true }).click()
  await wait(dlg.getByText('已回复', { exact: true }))
  assert.equal((await call('xiekai', 'GET', `/operations/tasks/${task.id}`)).last_feedback_at, null)
  await selectBox(dlg, '本次反馈位置')
  await employee.getByRole('option', { name: '确认需求', exact: true }).click()
  await dlg.getByPlaceholder('写几句话即可，例如：已和校方确认需求，明天下午提交方案。').fill('学校已确认首轮需求，接口字段已核对。')
  await dlg.locator('.el-checkbox').filter({ hasText: '确认需求' }).click()
  await dlg.getByRole('button', { name: '提交进展', exact: true }).click()
  await dlg.locator('.node-detail summary').filter({ hasText: '确认需求' }).click()
  await wait(dlg.locator('.node-entry').filter({ hasText: '学校已确认首轮需求' }))
  const row = await call('xiekai', 'GET', `/operations/tasks/${task.id}`)
  assert.equal(row.updates[0].node_id, task.nodes[0].id)
  assert.equal(row.progress, 50)
  assert.ok(await dlg.evaluate(el => el.scrollWidth <= el.clientWidth + 1))
  return shot(employee, 'iteration-mobile-feedback')
})

await step('ITER-04', '多任务表格与卡片使用同一记录', async () => {
  await manager.goto(`${WEB}/operations/${p.id}`)
  await manager.getByText('进度表', { exact: true }).click()
  await wait(manager.getByRole('cell', { name: '学校接口对接', exact: true }))
  await wait(manager.getByText(/学校已确认首轮需求/).first())
  return shot(manager, 'iteration-task-table')
})

await step('ITER-05', '以任务反馈模板提交结果并转入目标客户', async () => {
  await manager.goto(`${WEB}/operations/${p.id}`)
  await manager.getByRole('tab', { name: '市场开拓', exact: true }).click()
  await manager.getByRole('button', { name: '查看学校跟进：会议试点学校', exact: true }).click()
  const drawer = manager.getByRole('dialog', { name: '会议试点学校', exact: true })
  await drawer.getByRole('button', { name: '填写跟进反馈' }).click()
  await drawer.getByRole('radiogroup', { name: '完成情况', exact: true }).getByText('已完成', { exact: true }).click()
  await drawer.getByPlaceholder('写几句话即可，例如：已和校方确认需求，明天下午提交方案。').fill('已拜访学校，校方确认试点安排。')
  await drawer.getByPlaceholder('下一步要做什么，需要谁协调').fill('安排设备上线')
  await selectBox(drawer, '转入目标客户')
  await manager.getByRole('option', { name: '运营中', exact: true }).click()
  await drawer.getByRole('button', { name: '提交进展', exact: true }).click()
  await wait(drawer.getByText('已拜访学校，校方确认试点安排。', { exact: true }))
  await wait(drawer.getByText(/运营中 · 下一步/))
  const detail = await call('wujun', 'GET', `/operations/${p.id}`)
  assert.equal(detail.schools.find(s => s.id === school.id).stage, 'operating')
  assert.equal((await call('wujun', 'GET', `/operations/${p.id}/schools/${school.id}/visits`)).items[0].status, 'done')
  return shot(manager, 'iteration-school')
})

await step('ITER-06', '文字周报保存版本与来源回链', async () => {
  await manager.goto(`${WEB}/operations/${p.id}`)
  await manager.getByRole('tab', { name: '周报 / 月报', exact: true }).click()
  await manager.getByRole('textbox', { name: '汇总周次' }).fill('2026-W43')
  await manager.getByRole('button', { name: '查看汇总', exact: true }).click()
  await wait(manager.locator('.digest-text').filter({ hasText: '学校已确认首轮需求' }))
  await manager.getByRole('button', { name: '生成并保存本期报告', exact: true }).click()
  await wait(manager.getByText(/已保存版本 1/))
  const reports = await call('wujun', 'GET', '/operations/digest/reports', { project_id: p.id, period: '2026-W43' })
  assert.equal(reports.items.length, 1)
  const report = await call('wujun', 'GET', `/operations/digest/reports/${reports.items[0].id}`)
  assert.ok(report.text.includes('已拜访学校') && report.text.includes('学校已确认首轮需求'))
  return shot(manager, 'iteration-weekly-report')
})

await step('ITER-07', '合同默认本年，历史年份下钻与金额一致', async () => {
  const admin = await loginPage(browser, 'admin')
  await admin.goto(WEB + '/contracts')
  await wait(admin.getByRole('button', { name: /2026 年签约/ }))
  await selectBox(admin, '签订年份')
  const options = admin.getByRole('option')
  assert.ok(await options.count() >= 2)
  await admin.getByRole('option', { name: '全部年份', exact: true }).click()
  await wait(admin.getByRole('button', { name: /累计签约/ }))
  await admin.getByRole('button', { name: /本周签约/ }).click()
  await admin.waitForURL(/sign_period=week/)
  return shot(admin, 'iteration-contract-years')
})

await step('ITER-08', '无权账号不能访问任务和报告', async () => {
  assert.equal((await call('chenjing', 'GET', `/operations/tasks/${task.id}`, undefined, { raw: true })).status, 404)
  const reports = await call('wujun', 'GET', '/operations/digest/reports', { project_id: p.id })
  assert.equal((await call('xuhao', 'GET', `/operations/digest/reports/${reports.items[0].id}`, undefined, { raw: true })).status, 403)
})

await step('ITER-09', '节点完成后任务退出当前待办，历史记录仍可查', async () => {
  await employee.goto(taskUrl)
  const dlg = employee.getByRole('dialog', { name: '任务详情' })
  await dlg.locator('.el-radio-button').filter({ hasText: /^已完成$/ }).click()
  await dlg.getByPlaceholder('写几句话即可，例如：已和校方确认需求，明天下午提交方案。').fill('联调已完成，学校已确认结果。')
  await dlg.locator('.el-checkbox').filter({ hasText: '完成联调' }).click()
  await dlg.getByRole('button', { name: '提交进展', exact: true }).click()
  await wait(dlg.getByText('联调已完成，学校已确认结果。', { exact: false }).first())
  const row = await call('xiekai', 'GET', `/operations/tasks/${task.id}`)
  assert.equal(row.status, 'done'); assert.equal(row.progress, 100); assert.ok(row.updates.length >= 4)
  assert.equal((await call('xiekai', 'GET', '/operations')).todos.some(t => t.id === task.id), false)
  return shot(employee, 'iteration-completed-history')
})

await browser.close()
const result = save()
if (result.failed) process.exitCode = 1
