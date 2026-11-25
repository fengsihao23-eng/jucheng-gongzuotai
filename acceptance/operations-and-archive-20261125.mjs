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

async function fillDate(root, label, value) {
  const field = root.getByRole('combobox', { name: label, exact: true })
  await field.fill(value)
  await field.press('Enter')
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
  const opsProject = '智慧校园运营巡检'
  const ops = await call('admin', 'POST', '/operations', { name: opsProject, category: '智慧校园', is_key: true, owner_id: admin.id })
  const nextOwner = await call('xuhao', 'GET', '/auth/me')
  const newTask = '现场交付验收'
  const secondTask = '核对试点学校清单'
  const opsTask = '运营巡检设备登记'
  let keyPath

  await step('KEY-01', '从重点项目总览一次创建项目和多个任务，提交后留在总览', async () => {
    const requests = []
    desktop.on('request', request => requests.push({ path: new URL(request.url()).pathname, method: request.method() }))
    await desktop.goto(`${WEB}/market?tab=board`)
    const overview = desktop.getByRole('tab', { name: '重点项目总览', exact: true })
    await overview.waitFor()
    assert.equal(await overview.getAttribute('aria-selected'), 'true')
    assert.deepEqual(await desktop.getByRole('tab').allTextContents(), ['重点项目总览', '周报 / 月报'])
    await button(desktop, '新建重点项目').click()
    const form = desktop.getByRole('dialog', { name: '新建重点项目', exact: true })
    await form.getByLabel('重点项目名称').fill(name)
    await form.getByLabel('重点项目内容').fill('复核设备清单，确认试点学校的交付结果。')
    await fillDate(form, '项目准备日期', '2026-11-25')
    await fillDate(form, '项目完成日期', '2026-12-10')
    await button(form, '添加任务').click()
    const first = form.getByRole('region', { name: '任务 1', exact: true })
    await first.getByLabel('重点任务名称').fill(newTask)
    await first.getByText('工作要求、反馈与阶段节点', { exact: true }).click()
    await first.getByLabel('重点任务要求').fill('确认校方签收资料并登记验收结果。')
    await fillDate(first, '反馈截止时间', '2026-11-30 16:30')
    await button(first, '增加阶段节点').click()
    await first.getByLabel('阶段名称1').fill('确认校方签收')
    await first.getByText('工作要求、反馈与阶段节点', { exact: true }).click()
    await button(form, '添加任务').click()
    const second = form.getByRole('region', { name: '任务 2', exact: true })
    await second.getByLabel('重点任务名称').fill(secondTask)
    await second.getByRole('combobox', { name: '重点任务负责人', exact: true }).click()
    await desktop.getByRole('option', { name: nextOwner.name, exact: true }).click()
    await fillDate(second, '任务完成日期', '2026-12-08')
    await button(form, '添加任务').click()
    await button(form, '移除任务 3').click()
    await first.scrollIntoViewIfNeeded()
    const evidence = await capture(desktop, '01-新建项目同时添加任务', false)
    await button(form, '创建重点项目').click()
    await form.waitFor({ state: 'hidden' })
    const projectCard = button(desktop, `查看重点项目：${name}`)
    await projectCard.getByText('任务 0 / 2 项已完成', { exact: true }).waitFor()
    assert.equal(new URL(desktop.url()).pathname, '/market')
    assert.equal(requests.filter(x => x.method === 'POST' && x.path === '/api/v1/market/projects').length, 1)
    assert.ok(!requests.some(x => x.method === 'POST' && x.path.includes('/tasks')))
    await capture(desktop, '02-重点项目总览')
    await projectCard.click()
    await desktop.waitForURL(/\/market\/projects\/JC-/)
    keyPath = new URL(desktop.url()).pathname
    const saved = await call('admin', 'GET', keyPath)
    assert.deepEqual(saved.tasks.map(item => item.title), [newTask, secondTask])
    assert.equal(saved.tasks[0].owner_id, admin.id)
    assert.equal(saved.tasks[0].plan_date, '2026-12-10')
    assert.equal(saved.tasks[0].nodes[0].name, '确认校方签收')
    assert.equal(Date.parse(saved.tasks[0].feedback_due_at), Date.parse('2026-11-30T16:30:00+08:00'))
    assert.equal(saved.tasks[1].owner_id, nextOwner.id)
    assert.equal(saved.tasks[1].plan_date, '2026-12-08')
    assert.ok(!requests.some(x => x.path.startsWith('/api/v1/operations')))
    return evidence
  })

  await step('KEY-02', '项目内可查看两项任务并反馈，运营项目任务保持独立', async () => {
    await operations.goto(`${WEB}/operations/${ops.id}`)
    await createOpsTask(operations, opsTask)
    assert.equal(await card(operations, newTask).count(), 0)
    await desktop.goto(WEB + keyPath)
    const pane = desktop.getByRole('tabpanel', { name: '任务情况', exact: true })
    await card(pane, secondTask).waitFor()
    assert.equal(await card(pane, opsTask).count(), 0)
    await card(pane, newTask).click()
    const dialog = desktop.getByRole('dialog', { name: '任务情况', exact: true })
    await dialog.getByLabel('重点任务进展').fill('校方已确认现场验收结果。')
    await button(dialog, '提交进展').click()
    await desktop.getByText('进展已保存', { exact: true }).waitFor()
    await close(desktop, dialog)
    await card(pane, newTask).getByText('校方已确认现场验收结果。', { exact: true }).waitFor()
    await pane.getByText('进度表', { exact: true }).click()
    await pane.getByRole('row').filter({ hasText: secondTask }).waitFor()
    await button(pane, '进展汇总').click()
    const summary = desktop.getByRole('dialog', { name: '当前进展汇总', exact: true })
    await summary.getByRole('heading', { name, exact: true }).waitFor()
    await close(desktop, summary)
    await status(pane, /^已完成/).click()
    await pane.getByText('没有符合条件的任务', { exact: true }).waitFor()
    await status(pane, /^进行中/).click()
    await pane.getByText('任务卡', { exact: true }).click()
    return capture(desktop, '03-项目内任务情况')
  })

  await step('KEY-03', '手机可以在新建项目表单添加任务，项目内可直接反馈', async () => {
    await mobile.goto(WEB + '/market')
    await button(mobile, '新建重点项目').click()
    const form = mobile.getByRole('dialog', { name: '新建重点项目', exact: true })
    await button(form, '添加任务').click()
    await form.getByRole('region', { name: '任务 1', exact: true }).getByLabel('重点任务名称').waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    await button(form, '取消').click()
    await form.waitFor({ state: 'hidden' })
    await mobile.goto(WEB + keyPath)
    await card(mobile, newTask).click()
    const dialog = mobile.getByRole('dialog', { name: '任务情况', exact: true })
    await dialog.getByRole('heading', { name: newTask, exact: true }).waitFor()
    await close(mobile, dialog)
    return capture(mobile, '08-重点项目手机版')
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
