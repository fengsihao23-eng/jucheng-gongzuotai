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
const boardCreated = '教学终端月度维护'
const boardPanel = page => page.locator('section.panel').filter({ has: page.getByRole('heading', { name: '运营项目', exact: true }) })
const project = (page, name) => page.locator('.project-card').filter({ has: page.getByRole('heading', { name, exact: true }) })
const feedback = page => page.getByRole('region', { name: '项目跟踪反馈' })
const archiveButton = region => region.getByRole('button', { name: '归档', exact: true })

async function createProject(page, name, objective, key = true) {
  await page.getByRole('button', { name: '新建运营项目', exact: true }).click()
  const form = page.getByRole('dialog', { name: '新建运营项目', exact: true })
  await form.getByLabel('项目名称', { exact: true }).fill(name)
  await form.getByLabel('本阶段目标', { exact: true }).fill(objective)
  if (!key) await form.getByText('列为重点项目', { exact: true }).click()
  await form.getByRole('button', { name: '保存项目', exact: true }).click()
  await page.waitForURL(/\/operations\/\d+$/)
  return Number(new URL(page.url()).pathname.split('/').at(-1))
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
  for (const [name, category, objective, is_key] of [
    ['智慧校园试点', '智慧校园', '核对试点学校设备清单，安排首轮校方对接。', true],
    ['校园服务台', '其他运营', '整理各校区设备报修和服务反馈。', false],
  ]) {
    await call('admin', 'POST', '/operations', { name, category, objective, is_key, owner_id: admin.id })
  }

  let syncedId
  await step('OPS-01', '从运营项目新建后，已打开的市场看板自动显示同一项目', async () => {
    await desktop.goto(`${WEB}/market`)
    await operations.goto(`${WEB}/operations`)
    await project(desktop, '智慧校园试点').waitFor()
    await boardPanel(desktop).getByRole('heading', { name: '运营项目', exact: true }).waitFor()
    await desktop.getByRole('heading', { name: '重点事件', exact: true }).waitFor()
    syncedId = await createProject(operations, synced, '确认教学楼接入设备状态，汇总本轮巡检结果。', false)
    await project(desktop, synced).waitFor({ timeout: 7000 })
    assert.equal(await project(desktop, synced).count(), 1)
    await project(desktop, synced).getByText('已完成 0 / 0', { exact: true }).waitFor()
    await project(desktop, synced).click()
    await desktop.waitForURL(`${WEB}/operations/${syncedId}`)
  })

  await step('OPS-02', '市场看板新建与独立运营页相通，筛选和窄屏布局可用', async () => {
    await desktop.goto(`${WEB}/market`)
    await operations.goto(`${WEB}/operations`)
    const id = await createProject(desktop, boardCreated, '制定维护排期，按月汇总终端运行情况。')
    await project(operations, boardCreated).waitFor({ timeout: 7000 })
    assert.equal((await call('admin', 'GET', `/operations/${id}`)).name, boardCreated)
    await desktop.goto(`${WEB}/market`)
    const panel = boardPanel(desktop)
    await project(desktop, synced).waitFor()
    const keyFilter = panel.getByText('仅重点项目', { exact: true })
    await keyFilter.click()
    await project(desktop, synced).waitFor({ state: 'hidden' })
    await keyFilter.click()
    await project(desktop, synced).waitFor()
    const search = panel.getByRole('textbox', { name: '搜索运营项目' })
    await search.fill('巡检结果')
    await search.press('Tab')
    await project(desktop, '智慧校园试点').waitFor({ state: 'hidden' })
    await project(desktop, synced).waitFor()
    await search.fill('')
    await search.press('Tab')
    await project(desktop, '智慧校园试点').waitFor()
    await panel.locator('.el-select').filter({ has: desktop.getByRole('combobox', { name: '运营项目分类' }) }).click()
    await desktop.getByRole('option', { name: '其他运营', exact: true }).click()
    await project(desktop, '校园服务台').waitFor()
    await project(desktop, synced).waitFor({ state: 'hidden' })
    await desktop.goto(`${WEB}/market`)
    await project(desktop, synced).waitFor()
    await mobile.goto(`${WEB}/market`)
    await project(mobile, synced).waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    return capture(desktop, '01-运营项目同步看板')
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
