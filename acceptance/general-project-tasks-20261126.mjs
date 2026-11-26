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
const desktop = await loginPage(browser, 'admin', { width: 1440, height: 960 })
const mobile = await loginPage(browser, 'dingrui', { width: 390, height: 844 })
for (const page of [desktop, mobile]) page.setDefaultTimeout(12000)
const button = (root, name) => root.getByRole('button', { name, exact: true })
const dialog = (page, name) => page.getByRole('dialog', { name, exact: true })
const card = (root, title) => root.locator('.task-card').filter({ hasText: title })
const stage = (root, name) => root.locator('label.el-checkbox').filter({ hasText: name })
const board = desktop.getByRole('tabpanel', { name: '任务情况', exact: true })
const owner = await call('admin', 'GET', '/auth/me')
const assignee = await call('dingrui', 'GET', '/auth/me')
const projectName = '智慧校园二期设备交付'
const existingTitle = '现场点位复核'
const overdueTitle = '提交设备到货验收资料'
const mainTitle = '校方培训与交接确认'
const progress = '已完成校方培训，交接清单待双方确认。'
const completion = '设备交接清单已签收，培训资料已归档。'
let task, projectPath, generalPath

async function dateField(root, name, value) {
  const field = root.getByRole('combobox', { name, exact: true })
  await field.fill(value)
  await field.press('Enter')
}

async function capture(page, name) {
  await page.locator('.el-message').first().waitFor({ state: 'hidden' })
  await page.evaluate(() => document.fonts.ready)
  const file = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, file), animations: 'disabled' })
  return file
}

try {
  const project = await call('admin', 'POST', '/projects', { name: projectName, business_line: 'AF',
    customer_name: '校园项目验收测试', owner_id: owner.id, start_date: '2026-11-20', milestone_template: 'none' })
  projectPath = `/projects/${project.project_code}`
  generalPath = `/general-projects/${project.project_code}`
  await call('admin', 'POST', `${projectPath}/milestones`, { node_name: existingTitle, owner_id: owner.id,
    plan_date: '2026-12-04', status: 2, duty: '核对智慧教室设备安装位置和供电条件。' })
  await call('admin', 'POST', `${projectPath}/tasks`, { title: overdueTitle, owner_id: assignee.id, cadence: 'node',
    requirements: '汇总签收单、设备序列号和验收影像。', preparation_on: '2026-11-20', due_on: '2026-12-05',
    feedback_due_at: '2026-11-24T16:00:00+08:00', nodes: [{ name: '整理设备签收单', due_on: '2026-11-28' }] })

  await step('GEN-01', '一般项目表格打开任务页，已有流程节点直接显示为任务卡', async () => {
    await desktop.goto(WEB + '/general-projects')
    await desktop.getByText('表格', { exact: true }).click()
    await desktop.getByRole('row').filter({ hasText: project.project_code }).click()
    await desktop.waitForURL(url => url.pathname === generalPath)
    assert.ok((await desktop.getByRole('link', { name: '一般项目', exact: true }).getAttribute('class')).includes('active'))
    await card(board, existingTitle).waitFor()
    await button(desktop, '安排任务').waitFor()
    await desktop.getByText('标准控制流程', { exact: true }).waitFor()
  })

  await step('GEN-02', '安排负责人、反馈截止和阶段节点，保存后打开新任务', async () => {
    await button(desktop, '安排任务').click()
    const form = dialog(desktop, '安排任务')
    await form.getByRole('textbox', { name: '任务名称', exact: true }).fill(mainTitle)
    await form.getByRole('combobox', { name: '任务负责人', exact: true }).click()
    await desktop.getByRole('option', { name: assignee.name, exact: true }).click()
    await dateField(form, '任务完成日期', '2026-12-04')
    await form.getByRole('textbox', { name: '任务要求', exact: true }).fill('完成校方培训、设备交接和资料归档。')
    await form.getByText('按节点', { exact: true }).click()
    await dateField(form, '反馈截止时间', '2026-11-30 16:30')
    await button(form, '增加阶段节点').click()
    await form.getByRole('textbox', { name: '阶段名称1', exact: true }).fill('完成现场培训')
    await dateField(form, '阶段日期1', '2026-11-26')
    await button(form, '增加阶段节点').click()
    await form.getByRole('textbox', { name: '阶段名称2', exact: true }).fill('归档交接清单')
    await dateField(form, '阶段日期2', '2026-12-02')
    assert.ok(await form.locator('.node-edit').evaluateAll(rows => rows.every(row => row.scrollWidth <= row.clientWidth + 1)))
    const evidence = await capture(desktop, '02-安排任务')
    await button(form, '保存安排').click()
    const detail = dialog(desktop, '任务情况')
    await detail.getByRole('heading', { name: mainTitle, exact: true }).waitFor()
    task = (await call('admin', 'GET', `${projectPath}/tasks`)).tasks.find(item => item.title === mainTitle)
    assert.equal(task.owner_id, assignee.id)
    assert.equal(task.nodes.length, 2)
    assert.equal(Date.parse(task.feedback_due_at), Date.parse('2026-11-30T16:30:00+08:00'))
    assert.equal(new URL(desktop.url()).searchParams.get('task'), String(task.id))
    await button(detail, '关闭').click()
    await detail.waitFor({ state: 'hidden' })
    return evidence
  })

  await step('GEN-03', '任务卡、搜索、反馈筛选、进度表和进展汇总可切换', async () => {
    const filters = board.getByRole('group', { name: '按任务状态筛选' })
    await card(board, mainTitle).waitFor()
    await filters.getByRole('button', { name: /^逾期未反馈/ }).click()
    await card(board, overdueTitle).waitFor()
    assert.equal(await card(board, mainTitle).count(), 0)
    await filters.getByRole('button', { name: /^待反馈/ }).click()
    await card(board, mainTitle).getByText('反馈截止 2026-11-30 16:30', { exact: true }).waitFor()
    await filters.getByRole('button', { name: /^进行中/ }).click()
    const search = board.getByRole('textbox', { name: '任务搜索', exact: true })
    await search.fill(existingTitle)
    await card(board, existingTitle).waitFor()
    assert.equal(await card(board, overdueTitle).count(), 0)
    await search.clear()
    await board.getByText('进度表', { exact: true }).click()
    await board.getByRole('row').filter({ hasText: mainTitle }).getByText('2026-11-30 16:30', { exact: true }).waitFor()
    await capture(desktop, '03-进度表')
    await board.getByText('任务卡', { exact: true }).click()
    await button(board, '进展汇总').click()
    const summary = dialog(desktop, '当前进展汇总')
    await summary.getByText(/进行中 3 项 · 已完成 0 项 · 逾期未反馈 1 项/).waitFor()
    await desktop.keyboard.press('Escape')
    await summary.waitFor({ state: 'hidden' })
    return capture(desktop, '01-一般项目任务卡')
  })

  await step('GEN-04', '任务负责人从站内通知直达任务，在手机提交带阶段记录的反馈', async () => {
    const inbox = await call('dingrui', 'GET', '/notifications', { page_size: 100 })
    const notice = inbox.items.find(item => item.link === `${generalPath}?task=${task.id}`)
    assert.equal(notice?.title, '收到一般项目任务')
    await mobile.goto(WEB + notice.link)
    const detail = dialog(mobile, '任务情况')
    await detail.getByRole('heading', { name: mainTitle, exact: true }).waitFor()
    assert.equal(await button(detail, '调整安排').count(), 0)
    assert.equal(await button(mobile, '安排任务').count(), 0)
    await detail.getByRole('textbox', { name: '任务进展', exact: true }).fill(progress)
    await detail.getByRole('textbox', { name: '任务下一步', exact: true }).fill('确认签收后，将交接清单归档。')
    await stage(detail, '完成现场培训').click()
    await button(detail, '提交进展').click()
    await detail.getByText('2026-11-26 · 已完成 2026-11-26', { exact: true }).waitFor()
    await button(detail, '过程记录').click()
    const history = dialog(mobile, '过程记录')
    await history.getByText(new RegExp(progress)).waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    await mobile.keyboard.press('Escape')
    await history.waitFor({ state: 'hidden' })

    await card(board, mainTitle).click()
    const adminDetail = dialog(desktop, '任务情况')
    await adminDetail.getByText('2026-11-26 · 已完成 2026-11-26', { exact: true }).waitFor()
    const evidence = await capture(desktop, '04-任务反馈与阶段节点')
    await button(adminDetail, '关闭').click()
    return evidence
  })

  await step('GEN-05', '剩余阶段和任务完成后汇入已完成，原流程节点状态同步更新', async () => {
    const detail = dialog(mobile, '任务情况')
    await detail.getByRole('textbox', { name: '任务进展', exact: true }).fill(completion)
    await stage(detail, '归档交接清单').click()
    await detail.getByRole('radiogroup', { name: '任务状态', exact: true }).getByText('已完成', { exact: true }).click()
    await button(detail, '提交进展').click()
    await detail.getByText('任务已完成，可在过程记录查看历次反馈。', { exact: true }).waitFor()
    await button(detail, '关闭').click()
    const mobileBoard = mobile.getByRole('tabpanel', { name: '任务情况', exact: true })
    await mobileBoard.getByRole('button', { name: /^已完成/ }).click()
    await card(mobileBoard, mainTitle).getByText('节点 2 / 2', { exact: true }).waitFor()
    const data = await call('admin', 'GET', projectPath)
    assert.equal(data.milestones.find(item => item.id === task.id).status, 3)
    assert.equal(data.done_nodes, 1)
    return capture(mobile, '05-手机已完成任务')
  })
} finally {
  await browser.close()
  if (save().failed) process.exitCode = 1
}
