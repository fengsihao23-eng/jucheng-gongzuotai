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
const desktop = await loginPage(browser, 'admin', { width: 1560, height: 1000 })
const operations = await desktop.context().newPage()
const mobile = await loginPage(browser, 'admin', { width: 390, height: 844 })
for (const page of [desktop, operations, mobile]) page.setDefaultTimeout(10000)
const admin = await call('admin', 'GET', '/auth/me')
const boardPanel = page => page.locator('section.panel').filter({ has: page.getByRole('heading', { name: '任务情况', exact: true }) })
const card = (root, title) => root.locator('.task-card').filter({ hasText: title })
const status = (root, label) => root.getByRole('group', { name: '按任务状态筛选' }).getByRole('button', { name: label })
const button = (page, name) => page.getByRole('button', { name, exact: true })
const interaction = page => page.getByRole('dialog', { name: '互动反馈', exact: true })
const archiveDialog = page => page.getByRole('dialog', { name: '项目归档', exact: true })
const progress = page => interaction(page).getByRole('region', { name: '进展反馈', exact: true })

async function close(page, dialog) {
  await page.keyboard.press('Escape')
  await dialog.waitFor({ state: 'hidden' })
}

async function createOpsTask(page, title) {
  await button(page, '布置任务').click()
  const form = page.getByRole('dialog', { name: '布置重点任务', exact: true })
  await form.getByLabel('要完成什么').fill(title)
  await form.getByLabel('负责人', { exact: true }).click()
  await page.getByRole('option', { name: admin.name, exact: true }).click()
  await form.getByLabel('截止日期', { exact: true }).fill('2026-12-05')
  await form.getByLabel('截止日期', { exact: true }).press('Tab')
  await button(form, '发布任务').click()
  const dialog = page.getByRole('dialog', { name: '任务详情', exact: true })
  await dialog.getByRole('heading', { name: title, exact: true }).waitFor()
  await close(page, dialog)
}

async function chooseProject(page, title) {
  await boardPanel(page).locator('.el-select').filter({ has: page.getByRole('combobox', { name: '选择重点项目' }) }).click()
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

async function submitCompletion(page, content) {
  await progress(page).getByText('已完成', { exact: true }).click()
  await progress(page).getByRole('textbox', { name: '目前进度情况' }).fill(content)
  await button(interaction(page), '提交进展').click()
  await page.getByText('进展已提交', { exact: true }).waitFor()
}

async function archive(page, category) {
  const dialog = archiveDialog(page)
  const confirm = button(dialog, '确认归档')
  await confirm.waitFor()
  assert.ok(await confirm.isDisabled())
  for (const value of ['竞争对手拿走', '取消', '转化']) await dialog.getByText(value, { exact: true }).waitFor()
  await dialog.getByText(category, { exact: true }).click()
  await confirm.click()
  await dialog.waitFor({ state: 'hidden' })
}

async function verifyHistory(page, ...texts) {
  await button(interaction(page), '过程记录').click()
  const dialog = page.getByRole('dialog', { name: '过程记录', exact: true })
  for (const content of texts) await dialog.getByText(content, { exact: true }).waitFor()
  await close(page, dialog)
}

async function assertSingleFeedback(page) {
  await button(page, '互动反馈').waitFor()
  assert.equal(await button(page, '互动反馈').count(), 1)
  assert.equal(await button(page, '填写反馈').count(), 0)
}

try {
  const name = '智慧校园交付验收'
  const other = '教学终端部署'
  const opsProject = '智慧校园运营巡检'
  const keyFields = { owner_id: admin.id, preparation_on: '2026-11-19', due_on: '2026-12-10' }
  const key = await call('admin', 'POST', '/market/projects', { name, ...keyFields })
  await call('admin', 'POST', '/market/projects', { name: other, ...keyFields })
  const ops = await call('admin', 'POST', '/operations', { name: opsProject, category: '智慧校园', is_key: true, owner_id: admin.id })
  const keyPath = `/market/projects/${key.project_code}`
  const newTask = '现场交付验收'
  const opsTask = '运营巡检设备登记'
  const flagged = await call('admin', 'POST', `${keyPath}/tasks`, { title: '确认校方接口需求', ...keyFields, cadence: 'node' })
  await call('admin', 'POST', `${keyPath}/tasks/${flagged.id}/feedback`, { status: 2, content: '已确认对接范围，等待校方提供接口资料。',
    problems: '校方接口文档待补齐', next_step: '周五与校方二次确认', expected_revision: flagged.revision })
  await call('admin', 'POST', `${keyPath}/tasks`, { title: '核对试点学校设备清单', ...keyFields,
    requirements: '逐项核对教室网络设备及安装位置。', nodes: [{ name: '确认设备清单', due_on: '2026-12-03' }, { name: '校方复核', due_on: '2026-12-04' }] })
  await call('admin', 'POST', `${keyPath}/tasks`, { title: '整理项目培训安排', ...keyFields, requirements: '向各试点学校确认培训时间。' })

  await step('KEY-01', '市场看板使用重点项目任务，新建和反馈在重点项目内联动', async () => {
    const requests = []
    desktop.on('request', request => requests.push(new URL(request.url()).pathname))
    await desktop.goto(`${WEB}/market`)
    await chooseProject(desktop, name)
    const panel = boardPanel(desktop)
    await card(panel, flagged.title).getByText('有问题', { exact: true }).waitFor()
    await desktop.getByRole('heading', { name: '重点事件', exact: true }).waitFor()
    await button(panel, '安排任务').click()
    const form = desktop.getByRole('dialog', { name: '安排任务', exact: true })
    await form.getByLabel('重点任务名称').fill(newTask)
    await form.getByLabel('重点任务要求').fill('确认校方签收资料并登记验收结果。')
    await button(form, '保存安排').click()
    const taskDialog = desktop.getByRole('dialog', { name: '任务情况', exact: true })
    await taskDialog.getByRole('heading', { name: newTask, exact: true }).waitFor()
    await close(desktop, taskDialog)
    await card(panel, newTask).waitFor()
    const evidence = await capture(desktop, '01-重点项目跟进看板')
    await card(panel, newTask).click()
    await taskDialog.getByLabel('重点任务进展').fill('校方已确认现场验收结果。')
    await button(taskDialog, '提交进展').click()
    await desktop.getByText('进展已保存', { exact: true }).waitFor()
    await close(desktop, taskDialog)
    await panel.getByRole('link', { name: '项目详情', exact: true }).click()
    await desktop.waitForURL(WEB + keyPath)
    await card(desktop, newTask).getByText('校方已确认现场验收结果。', { exact: true }).waitFor()
    assert.ok(!requests.some(url => url.startsWith('/api/v1/operations')))
    return evidence
  })

  await step('KEY-02', '运营任务与重点项目数据分开，任务卡、进度表和筛选可用', async () => {
    await operations.goto(`${WEB}/operations/${ops.id}`)
    await createOpsTask(operations, opsTask)
    await card(operations, opsTask).waitFor()
    assert.equal(await card(operations, newTask).count(), 0)
    assert.ok(!(await call('admin', 'GET', `/operations/${ops.id}`)).tasks.some(task => task.title === newTask))
    await desktop.goto(`${WEB}/market`)
    await chooseProject(desktop, name)
    const panel = boardPanel(desktop)
    await card(panel, newTask).waitFor()
    assert.equal(await card(panel, opsTask).count(), 0)
    await panel.locator('.el-select').filter({ has: desktop.getByRole('combobox', { name: '选择重点项目' }) }).click()
    assert.equal(await desktop.getByRole('option', { name: opsProject, exact: true }).count(), 0)
    await desktop.getByRole('option', { name: other, exact: true }).click()
    await panel.getByText('还没有安排任务', { exact: true }).waitFor()
    await chooseProject(desktop, name)
    const search = panel.getByRole('textbox', { name: '重点任务搜索' })
    await search.fill(newTask)
    await card(panel, newTask).waitFor()
    assert.equal(await card(panel, flagged.title).count(), 0)
    await panel.getByText('进度表', { exact: true }).click()
    await panel.getByRole('row').filter({ hasText: newTask }).waitFor()
    await button(panel, '进展汇总').click()
    const summary = desktop.getByRole('dialog', { name: '当前进展汇总', exact: true })
    await summary.getByRole('heading', { name, exact: true }).waitFor()
    await close(desktop, summary)
    await desktop.goto(WEB + keyPath)
    const detailPane = desktop.getByRole('tabpanel', { name: '任务情况', exact: true })
    await status(detailPane, /^已完成/).click()
    await detailPane.getByText('没有符合条件的任务', { exact: true }).waitFor()
    await status(detailPane, /^进行中/).click()
    return capture(desktop, '02-重点项目任务卡')
  })

  await step('KEY-03', '手机显示重点项目任务和原项目内反馈入口', async () => {
    await mobile.goto(WEB + keyPath)
    await card(mobile, newTask).waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    await card(mobile, newTask).click()
    const dialog = mobile.getByRole('dialog', { name: '任务情况', exact: true })
    await dialog.getByRole('heading', { name: newTask, exact: true }).waitFor()
    await close(mobile, dialog)
    return capture(mobile, '03-重点项目手机版')
  })

  await step('ARCHIVE-01', '商机列表可直接发起归档，旧反馈可在互动反馈的过程记录查看', async () => {
    const source = await call('admin', 'GET', '/business-workbook/opportunities')
    const title = '实验学校网络扩容'
    const item = source.items.find(row => row.name === title)
    const previous = '旧版入口记录的校方沟通进展。'
    const completed = '已确认商机由其他供应商接手。'
    await call('admin', 'POST', `/business-workbook/opportunities/rows/${item.sheet_id}/${item.row}/feedback`, {
      revision: source.source.revision, status: 'doing', content: previous,
    })
    await desktop.goto(`${WEB}/opportunities`)
    await selectArchiveFilter(desktop, '未归档')
    await desktop.waitForURL(/archive_category=unarchived/)
    const row = desktop.getByRole('row').filter({ hasText: title })
    const listShot = await capture(desktop, '04-商机列表归档入口')
    await button(row, '归档').click()
    await interaction(desktop).getByText('提交完成结果后即可选择归档类别。', { exact: true }).waitFor()
    assert.equal(await archiveDialog(desktop).count(), 0)
    await submitCompletion(desktop, completed)
    await archive(desktop, '竞争对手拿走')
    await close(desktop, interaction(desktop))
    await desktop.getByText('暂无符合条件的商机', { exact: true }).waitFor()
    await selectArchiveFilter(desktop, '竞争对手拿走')
    await button(row, title).click()
    await desktop.getByText('已归档 · 竞争对手拿走', { exact: true }).waitFor()
    await assertSingleFeedback(desktop)
    await button(desktop, '互动反馈').click()
    await verifyHistory(desktop, previous, completed)
    await close(desktop, interaction(desktop))
    await capture(desktop, '05-商机详情互动反馈')
    return listShot
  })

  await step('ARCHIVE-02', '招标项目已完成时列表归档直接选类别，标段共享归档状态', async () => {
    const title = '校园设备招标'
    await desktop.goto(`${WEB}/tender-projects`)
    await button(desktop, `查看项目：${title}`).click()
    await assertSingleFeedback(desktop)
    await button(desktop, '互动反馈').click()
    await submitCompletion(desktop, '已确认本轮招标取消。')
    await close(desktop, interaction(desktop))
    await button(desktop, '返回项目列表').click()
    const row = desktop.getByRole('row').filter({ hasText: title })
    await button(row, '归档').click()
    await button(archiveDialog(desktop), '确认归档').waitFor()
    const evidence = await capture(desktop, '06-招标项目归档类别', false)
    await archive(desktop, '取消')
    await close(desktop, interaction(desktop))
    await selectArchiveFilter(desktop, '取消')
    await row.getByText('取消', { exact: true }).waitFor()
    const rows = (await call('admin', 'GET', '/business-workbook/tender', { archive_category: 'canceled' })).items
    assert.ok(rows.length > 1 && rows.every(item => item.group_row === rows[0].group_row && item.archive_category === 'canceled'))
    return evidence
  })

  await step('ARCHIVE-03', '在建详情可归档为转化，完成反馈保留在互动反馈', async () => {
    await mobile.goto(`${WEB}/tender-projects`)
    await mobile.getByRole('button', { name: /^在建项目/ }).click()
    await button(mobile, '查看项目：信息教室设备安装').click()
    await assertSingleFeedback(mobile)
    await button(mobile, '归档').click()
    const content = '已与项目组确认实施结果并完成跟踪。'
    await submitCompletion(mobile, content)
    await archive(mobile, '转化')
    await verifyHistory(mobile, content)
    await close(mobile, interaction(mobile))
    await mobile.getByText('已归档 · 转化', { exact: true }).waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    return capture(mobile, '07-在建归档结果')
  })
} finally {
  await browser.close()
  process.exitCode = save().failed ? 1 : 0
}
