import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, OUT, WEB, call, loginPage, save, step } from './lib.mjs'

for (const url of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname))
assert.equal((await fetch(`${API}/health`).then(response => response.json())).env, 'test')

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const desktop = await loginPage(browser, 'admin', { width: 1560, height: 1060 })
const operations = await desktop.context().newPage()
const mobile = await loginPage(browser, 'admin', { width: 390, height: 844 })
for (const page of [desktop, operations, mobile]) page.setDefaultTimeout(10000)
const admin = await call('admin', 'GET', '/auth/me')
const synced = '校园网络巡检'
const newProject = '教学终端月度维护'
const boardPanel = page => page.locator('section.panel').filter({ has: page.getByRole('heading', { name: '任务情况', exact: true }) })
const taskCard = (root, title) => root.locator('.task-card').filter({ hasText: title })
const status = (root, label) => root.getByRole('group', { name: '按任务状态筛选' }).getByRole('button', { name: label })
const taskDialog = page => page.getByRole('dialog', { name: '任务详情', exact: true })
const feedback = page => page.getByRole('region', { name: '项目跟踪反馈' })
const archiveButton = region => region.getByRole('button', { name: '归档', exact: true })

async function createProject(page, name, objective) {
  await page.getByRole('button', { name: '新建运营项目', exact: true }).click()
  const form = page.getByRole('dialog', { name: '新建运营项目', exact: true })
  await form.getByLabel('项目名称', { exact: true }).fill(name)
  await form.getByLabel('本阶段目标', { exact: true }).fill(objective)
  await form.getByText('列为重点项目', { exact: true }).click()
  await form.getByRole('button', { name: '保存项目', exact: true }).click()
  await page.waitForURL(/\/operations\/\d+$/)
  return Number(new URL(page.url()).pathname.split('/').at(-1))
}

async function createTask(page, root, title) {
  await root.getByRole('button', { name: '布置任务', exact: true }).click()
  const form = page.getByRole('dialog', { name: '布置重点任务', exact: true })
  await form.getByLabel('要完成什么').fill(title)
  await form.getByLabel('完成要求').fill('检查设备运行情况，记录发现的问题。')
  await form.getByLabel('负责人', { exact: true }).click()
  await page.getByRole('option', { name: admin.name, exact: true }).click()
  await form.getByLabel('截止日期', { exact: true }).fill('2026-12-05')
  await form.getByLabel('截止日期', { exact: true }).press('Tab')
  await form.getByRole('button', { name: '发布任务', exact: true }).click()
  await taskDialog(page).getByRole('heading', { name: title, exact: true }).waitFor()
  await taskDialog(page).getByRole('button', { name: '关闭', exact: true }).click()
  await taskDialog(page).waitFor({ state: 'hidden' })
}

async function chooseProject(page, title) {
  await boardPanel(page).locator('.el-select').filter({ has: page.getByRole('combobox', { name: '选择运营项目' }) }).click()
  await page.getByRole('option', { name: title, exact: true }).click()
}

async function selectArchiveFilter(page, value) {
  await page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '归档分类', exact: true }) }).click()
  await page.getByRole('option', { name: value, exact: true }).click()
}

async function capture(page, name, fullPage = true) {
  await page.locator('.el-message').first().waitFor({ state: 'hidden' })
  const file = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, file), fullPage, animations: 'disabled' })
  return file
}

async function submitCompletion(page) {
  const region = feedback(page)
  await region.getByRole('button', { name: '填写反馈', exact: true }).click()
  assert.equal(await archiveButton(region).count(), 0)
  await region.getByText('已完成', { exact: true }).click()
  assert.ok(await archiveButton(region).isDisabled())
  await region.getByRole('textbox', { name: '项目本次进展' }).fill('已和演示项目组确认处理结果并完成跟踪。')
  await region.getByRole('button', { name: '提交反馈', exact: true }).click()
  await archiveButton(region).click()
  return page.getByRole('dialog', { name: '项目归档', exact: true })
}

async function archive(dialog, category) {
  const confirm = dialog.getByRole('button', { name: '确认归档', exact: true })
  await confirm.waitFor()
  assert.ok(await confirm.isDisabled())
  for (const value of ['竞争对手拿走', '取消', '转化']) await dialog.getByText(value, { exact: true }).waitFor()
  await dialog.getByText(category, { exact: true }).click()
  await confirm.click()
  await dialog.waitFor({ state: 'hidden' })
}

try {
  const campus = await call('admin', 'POST', '/operations', { name: '智慧校园试点', category: '智慧校园',
    objective: '核对试点学校设备清单，安排首轮校方对接。', is_key: true, owner_id: admin.id })
  const blocked = await call('admin', 'POST', `/operations/${campus.id}/tasks`, { title: '校方接口需求确认',
    owner_id: admin.id, due_date: '2026-12-02', description: '整理接口和上线需求。' })
  await call('admin', 'POST', `/operations/tasks/${blocked.id}/feedback`, { status: 'blocked', content: '已确认对接范围，等待校方提供接口资料。',
    incomplete_reason: '校方接口文档待补齐', next_step: '周五与校方二次确认', expected_revision: blocked.revision })
  await call('admin', 'POST', `/operations/${campus.id}/tasks`, { title: '试点学校设备清单核对', owner_id: admin.id, due_date: '2026-12-04',
    description: '逐项核对教室网络设备及安装位置。', nodes: [{ name: '确认设备清单', due_date: '2026-12-03' }, { name: '校方复核', due_date: '2026-12-04' }] })

  await step('OPS-01', '市场看板显示任务情况，运营项目布置和市场反馈双向同步', async () => {
    await desktop.goto(`${WEB}/market`)
    const panel = boardPanel(desktop)
    await taskCard(panel, blocked.title).getByText('遇到阻碍', { exact: true }).waitFor()
    assert.equal(await panel.locator('.project-card').count(), 0)
    await desktop.getByRole('heading', { name: '重点事件', exact: true }).waitFor()
    await operations.goto(`${WEB}/operations/${campus.id}`)
    await createTask(operations, operations, synced)
    await taskCard(panel, synced).waitFor({ timeout: 7000 })
    assert.equal(await taskCard(panel, synced).count(), 1)
    const evidence = await capture(desktop, '01-运营项目任务情况')
    await taskCard(panel, synced).click()
    const dialog = taskDialog(desktop)
    await dialog.getByText('已完成', { exact: true }).click()
    await dialog.getByLabel('完成结果', { exact: true }).fill('已核对教学楼设备状态，完成本轮巡检。')
    await dialog.getByRole('button', { name: '提交进展', exact: true }).click()
    await desktop.getByText('进展已反馈', { exact: true }).waitFor()
    await desktop.keyboard.press('Escape')
    await dialog.waitFor({ state: 'hidden' })
    assert.equal(new URL(desktop.url()).pathname, '/market')
    await status(panel, /^已完成/).click()
    await taskCard(panel, synced).waitFor()
    const operationsPane = operations.getByRole('tabpanel', { name: '任务情况', exact: true })
    await status(operationsPane, /^已完成/).click()
    await taskCard(operationsPane, synced).getByText('已核对教学楼设备状态，完成本轮巡检。', { exact: true }).waitFor({ timeout: 7000 })
    return evidence
  })

  await step('OPS-02', '新建运营项目进入项目选择，任务可按项目、关键词和视图切换', async () => {
    await desktop.goto(`${WEB}/market`)
    await operations.goto(`${WEB}/operations`)
    await createProject(operations, newProject, '制定维护排期，按月汇总终端运行情况。')
    const panel = boardPanel(desktop)
    await chooseProject(desktop, newProject)
    await panel.getByText('还没有安排任务', { exact: true }).waitFor()
    const title = '核对桌面终端库存'
    await createTask(desktop, panel, title)
    await taskCard(operations, title).waitFor({ timeout: 7000 })
    const search = panel.getByRole('textbox', { name: '搜索任务' })
    await search.fill('不应匹配')
    await panel.getByText('没有符合筛选条件的任务', { exact: true }).waitFor()
    await search.fill('')
    await panel.getByText('进度表', { exact: true }).click()
    await panel.getByRole('row').filter({ hasText: title }).waitFor()
    await panel.getByRole('button', { name: '进展汇总', exact: true }).click()
    const summary = desktop.getByRole('dialog', { name: '当前进展汇总', exact: true })
    await summary.getByRole('region', { name: '项目任务概览', exact: true }).waitFor()
    await desktop.keyboard.press('Escape')
    await summary.waitFor({ state: 'hidden' })
    await chooseProject(desktop, campus.name)
    await taskCard(panel, blocked.title).waitFor()
    assert.equal(await taskCard(panel, title).count(), 0)
  })

  await step('OPS-03', '任务情况在手机可用并遵循项目成员权限', async () => {
    await mobile.goto(`${WEB}/market`)
    await taskCard(boardPanel(mobile), blocked.title).waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    const evidence = await capture(mobile, '05-手机任务情况')
    const staff = await call('zhengyu', 'GET', '/auth/me')
    await call('admin', 'POST', `/operations/${campus.id}/members`, { user_id: staff.id, role: 'viewer' })
    const reader = await loginPage(browser, 'zhengyu')
    await reader.goto(`${WEB}/market`)
    const panel = boardPanel(reader)
    await taskCard(panel, blocked.title).waitFor()
    assert.equal(await panel.getByRole('button', { name: '布置任务', exact: true }).count(), 0)
    await panel.locator('.el-select').filter({ has: reader.getByRole('combobox', { name: '选择运营项目' }) }).click()
    await reader.getByRole('option', { name: campus.name, exact: true }).waitFor()
    assert.equal(await reader.getByRole('option', { name: newProject, exact: true }).count(), 0)
    await reader.context().close()
    return evidence
  })

  await step('ARCHIVE-01', '商机完成后可选类别，归档结果随列表筛选和刷新保留', async () => {
    await desktop.goto(`${WEB}/opportunities`)
    await selectArchiveFilter(desktop, '未归档')
    await desktop.waitForURL(/archive_category=unarchived/)
    const name = '实验学校网络扩容'
    await desktop.getByRole('button', { name, exact: true }).click()
    const dialog = await submitCompletion(desktop)
    await dialog.getByRole('button', { name: '返回', exact: true }).click()
    await archiveButton(feedback(desktop)).click()
    await archive(dialog, '竞争对手拿走')
    await feedback(desktop).getByText('已归档 · 竞争对手拿走', { exact: true }).waitFor()
    await desktop.reload()
    await feedback(desktop).getByText('已归档 · 竞争对手拿走', { exact: true }).waitFor()
    await desktop.getByRole('button', { name: '返回商机列表' }).click()
    await desktop.getByText('暂无符合条件的商机', { exact: true }).waitFor()
    await selectArchiveFilter(desktop, '竞争对手拿走')
    await desktop.getByRole('row').filter({ hasText: name }).getByText('竞争对手拿走', { exact: true }).waitFor()
    return capture(desktop, '02-商机归档筛选')
  })

  await step('ARCHIVE-02', '招标项目可从互动反馈归档，多个标段共享类别', async () => {
    await desktop.goto(`${WEB}/tender-projects`)
    const name = '校园设备招标'
    await desktop.getByRole('button', { name: `查看项目：${name}`, exact: true }).click()
    await desktop.getByRole('button', { name: '互动反馈', exact: true }).click()
    const interaction = desktop.getByRole('dialog', { name: '互动反馈', exact: true })
    const region = interaction.getByRole('region', { name: '进展反馈', exact: true })
    await region.getByText('已完成', { exact: true }).click()
    assert.ok(await archiveButton(region).isDisabled())
    await region.getByRole('textbox', { name: '目前进度情况' }).fill('已确认本轮招标取消。')
    await interaction.getByRole('button', { name: '提交进展', exact: true }).click()
    await archiveButton(region).click()
    const dialog = desktop.getByRole('dialog', { name: '项目归档', exact: true })
    await dialog.getByRole('button', { name: '确认归档', exact: true }).waitFor()
    const evidence = await capture(desktop, '03-招标项目归档类别', false)
    await archive(dialog, '取消')
    await region.getByText('已归档 · 取消', { exact: true }).waitFor()
    await interaction.getByRole('button', { name: '关闭', exact: true }).click()
    await feedback(desktop).getByText('已归档 · 取消', { exact: true }).waitFor()
    await desktop.getByRole('button', { name: '返回项目列表' }).click()
    await selectArchiveFilter(desktop, '取消')
    await desktop.getByRole('row').filter({ hasText: name }).getByText('取消', { exact: true }).waitFor()
    const rows = (await call('admin', 'GET', '/business-workbook/tender', { archive_category: 'canceled' })).items
    assert.ok(rows.length > 1 && rows.every(row => row.group_row === rows[0].group_row && row.archive_category === 'canceled'))
    return evidence
  })

  await step('ARCHIVE-03', '在建项目完成后归为转化，详情保留过程记录', async () => {
    await mobile.goto(`${WEB}/tender-projects`)
    await mobile.getByRole('button', { name: /^在建项目/ }).click()
    await mobile.getByRole('button', { name: '查看项目：信息教室设备安装', exact: true }).click()
    await archive(await submitCompletion(mobile), '转化')
    await feedback(mobile).getByText('已归档 · 转化', { exact: true }).waitFor()
    await feedback(mobile).getByText('已和演示项目组确认处理结果并完成跟踪。', { exact: true }).waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    const evidence = await capture(mobile, '04-在建项目转化归档')
    await mobile.getByRole('button', { name: '返回项目列表' }).click()
    await selectArchiveFilter(mobile, '转化')
    await mobile.getByRole('row').filter({ hasText: '信息教室设备安装' }).getByText('转化', { exact: true }).waitFor()
    return evidence
  })
} finally {
  await browser.close()
  process.exitCode = save().failed ? 1 : 0
}
