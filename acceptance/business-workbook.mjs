import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, call, loginPage, shot, step, save } from './lib.mjs'

const health = await fetch(API + '/health').then(r => r.json())
assert.equal(health.env, 'test', 'Use an isolated test database')
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(API).hostname))
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const desktop = await loginPage(browser, 'admin')
const mobile = await loginPage(browser, 'admin', { width: 390, height: 844 })
const paths = [['contracts', '/contracts'], ['collections', '/collections'], ['opportunities', '/opportunities'],
  ['tender', '/tender-projects']]
const mixedReads = []
desktop.on('request', r => { if (/\/api\/v1\/(?:contracts(?:\?|$)|collections(?:\?|$)|opportunities(?:\?|$)|projects(?:\?|$))/.test(r.url())) mixedReads.push(r.url()) })
async function seeSource(page, mode, revision) {
  const button = page.getByRole('button', { name: '表格同步', exact: true })
  if (mode === 'opportunities') await button.click()
  await page.getByText('版本 ' + revision, { exact: true }).waitFor()
  if (mode === 'opportunities') await button.click()
}

for (const [mode, path] of paths) {
  await step('SOURCE-' + mode, '固定来源页面 ' + mode, async () => {
    const result = await call('admin', 'GET', '/business-workbook/' + mode)
    assert.equal(result.active, true)
    await desktop.goto(WEB + path)
    await seeSource(desktop, mode, result.source.revision)
    if (mode === 'collections') {
      await desktop.getByRole('heading', { name: '回款情况', exact: true }).waitFor()
      assert.equal(await desktop.getByRole('link', { name: '项目报告', exact: true }).count(), Math.min(30, result.total))
    } else if (mode === 'tender') {
      await desktop.getByRole('heading', { name: '招标项目', exact: true }).waitFor()
      assert.equal(await desktop.getByRole('button', { name: /查看项目：/ }).count(), result.summary.groups)
    } else {
      await desktop.getByText(result.items[0].name, { exact: true }).first().waitFor()
      assert.equal(await desktop.getByRole('button', { name: '查看详情', exact: true }).count(), Math.min(30, result.total))
    }
    assert.ok(await desktop.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    await shot(desktop, 'source-' + mode)
    await mobile.goto(WEB + path)
    await seeSource(mobile, mode, result.source.revision)
    await shot(mobile, 'source-mobile-' + mode)
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), JSON.stringify(await mobile.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, elements: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 2 && e.getBoundingClientRect().width > 200 && !e.closest('.el-table')).slice(0, 12).map(e => ({ tag: e.tagName, cls: e.className, width: e.getBoundingClientRect().width })) }))))
  })
}

await step('SOURCE-detail', '点击项目打开业务详情且保留当前数据', async () => {
  const result = await call('admin', 'GET', '/business-workbook/contracts')
  await desktop.goto(WEB + '/contracts')
  const item = result.items[0]
  await desktop.getByRole('button', { name: '查看详情', exact: true }).first().click()
  await desktop.getByRole('heading', { name: '基本信息', exact: true }).waitFor()
  const detail = await call('admin', 'GET', `/business-workbook/contracts/items/${item.sheet_id}/${item.row}`, { revision: result.source.revision })
  for (const field of ['name', 'amount', 'received', 'outstanding', 'customer', 'owner']) assert.equal(detail.contract[field] ?? null, item[field] ?? null)
  assert.equal(await desktop.getByRole('dialog').count(), 0)
  await shot(desktop, 'business-contract-detail')
})

await step('SOURCE-collections-sheets', '回款情况合并指定的两张子表并排除其他来源', async () => {
  const result = await call('admin', 'GET', '/business-workbook/collections')
  assert.deepEqual(result.sheets.map(s => s.profile), ['sales', 'older'])
  assert.equal(result.total, 144)
  assert.equal(result.summary.amount.value, '121541140.46')
  const foreign = await call('admin', 'GET', '/business-workbook/collections', { sheet: 'nb2k6s' }, { raw: true })
  assert.equal(foreign.status, 404)
})

await step('SOURCE-search', '筛选合计与结果保持一致', async () => {
  await desktop.goto(WEB + '/contracts')
  const result = await call('admin', 'GET', '/business-workbook/contracts', { keyword: '卫生健康局' })
  await desktop.getByRole('textbox', { name: '合同搜索', exact: true }).fill('卫生健康局')
  await desktop.getByRole('button', { name: '查询', exact: true }).click()
  await desktop.waitForFunction(n => document.querySelector('.workbook-kpis .kpi .v')?.textContent.trim() === String(n), result.total)
  assert.equal(await desktop.getByRole('button', { name: '查看详情', exact: true }).count(), result.total)
})

await step('SOURCE-board-layout', '保留原看板五张指标卡及图表布局，汇总其他业务模块', async () => {
  const overview = await call('admin', 'GET', '/board/overview', { period: '2026-11' })
  assert.equal(overview.payment.amount, '839395.49')
  assert.equal(overview.sign.year.amount, '34152979.72')
  assert.equal(overview.sign.year.count, 69)
  assert.equal(overview.tender.bidding_count, 10)
  assert.equal(overview.tender.building_count, 21)
  assert.ok(overview.key_projects && overview.profit && overview.submission)
  await desktop.goto(WEB + '/board')
  await desktop.getByText('83.9', { exact: true }).first().waitFor()
  assert.equal(await desktop.locator('.jc-grid.c5 > .kpi').count(), 5)
  for (const label of ['① 回款目标达成', '本月每日累计回款 vs 目标线', '月度经营历史', '② 重点项目进展', '⑥ 周报 / 月报提交率', '毛利摘要']) {
    await desktop.getByRole('heading', { name: label, exact: true }).waitFor()
  }
  assert.equal(await desktop.getByText('各子表经营明细', { exact: true }).count(), 0)
  await shot(desktop, 'original-board-layout')
  await mobile.goto(WEB + '/board')
  await mobile.getByText('83.9', { exact: true }).first().waitFor()
  await shot(mobile, 'original-board-mobile')
})
await step('SOURCE-board-drill', '看板签约与回款下钻和模块明细合计完全一致', async () => {
  await desktop.goto(WEB + '/board')
  await desktop.locator('.jc-grid.c5 .kpi').filter({ hasText: '年度累计业绩' }).click()
  await desktop.getByText('34,152,979.72', { exact: true }).first().waitFor()
  await desktop.waitForFunction(() => document.querySelector('.workbook-kpis .kpi .v')?.textContent.trim() === '69')
  await desktop.goto(WEB + '/board')
  await desktop.locator('.jc-grid.c5 .kpi').filter({ hasText: '当月回款' }).click()
  await desktop.getByText('839,395.49', { exact: true }).first().waitFor()
  assert.equal(await desktop.getByRole('link', { name: '查看详情', exact: true }).count(), 2)
  await desktop.getByRole('link', { name: '查看详情', exact: true }).first().click()
  await desktop.getByRole('heading', { name: '回款安排', exact: true }).waitFor()
})
await step('SOURCE-collection-plans', '回款卡片只读原表计划，进入具体项目报告；签约卡片移入经营业绩', async () => {
  const report = await call('admin', 'GET', '/business-workbook/collections', { unpaid_only: true })
  assert.deepEqual(report.collection_cards.map(c => c.amount), ['19365474.13', '3645420.00'])
  for (const page of [desktop, mobile]) {
    await page.goto(WEB + '/collections')
    await page.getByRole('heading', { name: '未收项目清单', exact: true }).waitFor()
    assert.equal(await page.locator('.year-filter').count(), 0)
    assert.equal(await page.getByText('已收', { exact: true }).count(), 0)
    for (const card of report.collection_cards) {
      await page.getByRole('button', { name: new RegExp(card.title) }).click()
      const inlinePanel = page.getByRole('region', { name: card.title + '明细', exact: true })
      await inlinePanel.waitFor()
      assert.equal(await page.getByRole('dialog', { name: card.title, exact: true }).count(), 0)
      await inlinePanel.getByRole('link', { name: '项目反馈 / 报告' }).first().click()
      await page.getByRole('heading', { name: '对应合同', exact: true }).waitFor()
      await page.getByRole('button', { name: '返回回款情况', exact: true }).click()
    }
    await page.goto(WEB + '/contracts')
    const view = await call('admin', 'GET', '/business-workbook/contracts')
    for (const [key, name] of [['month', '当月合同金额'], ['week', '本周合同金额']]) {
      await page.getByRole('button', { name: new RegExp(name) }).click()
      await page.waitForFunction(n => document.querySelector('.workbook-kpis .kpi .v')?.textContent.trim() === String(n), view.quick_stats[key].count)
    }
    await page.getByRole('button', { name: '查看全年业绩', exact: true }).click()
    await page.waitForFunction(n => document.querySelector('.workbook-kpis .kpi .v')?.textContent.trim() === String(n), view.summary.count)
  }
})
await step('SOURCE-tender-project-cards', '招标与在建分开切换，合并项目点开保留各标段', async () => {
  const base = '/business-workbook/tender'
  const tender = await call('admin', 'GET', base)
  const building = await call('admin', 'GET', base, { sheet: tender.sheets.find(s => s.profile === 'pending').id })
  const lot = tender.items.find(row => row.row !== row.group_row)
  assert.equal(tender.summary.groups, 10)
  assert.equal(tender.total, 11)
  assert.equal(building.summary.groups, 21)
  assert.ok(lot)
  for (const page of [desktop, mobile]) {
    await page.goto(WEB + '/tender-projects')
    const cards = page.getByRole('button', { name: /查看项目：/ })
    const tenderModule = page.getByRole('button', { name: /^招标项目/ })
    const buildingModule = page.getByRole('button', { name: /^在建项目/ })
    const list = page.locator('#tender-project-list')
    await list.getByRole('heading', { name: '招标项目', exact: true }).waitFor()
    await page.waitForFunction(n => document.querySelectorAll('.project-card').length === n, tender.summary.groups)
    assert.equal(await tenderModule.getAttribute('aria-pressed'), 'true')
    assert.equal(await page.getByText('筛选来源明细', { exact: true }).count(), 0)
    assert.equal(await page.getByText('原表字段', { exact: true }).count(), 0)
    await page.getByRole('button', { name: '查看项目：' + lot.name, exact: true }).click()
    const drawer = page.getByRole('dialog', { name: lot.name, exact: true })
    await drawer.getByRole('heading', { name: '标段明细 1', exact: true }).waitFor()
    await drawer.getByRole('heading', { name: '标段明细 2', exact: true }).waitFor()
    await page.keyboard.press('Escape')
    await drawer.waitFor({ state: 'hidden' })
    await buildingModule.click()
    await list.getByRole('heading', { name: '在建项目', exact: true }).waitFor()
    assert.equal(await cards.count(), building.summary.groups)
    assert.equal(await page.getByRole('button', { name: '查看项目：' + lot.name, exact: true }).count(), 0)
    const search = page.getByRole('textbox', { name: '搜索项目', exact: true })
    await search.fill(building.items[0].name)
    assert.ok(await cards.count() < building.summary.groups)
    await cards.first().click()
    const project = page.getByRole('dialog', { name: building.items[0].name, exact: true })
    await project.getByText('合同金额', { exact: true }).waitFor()
    await page.keyboard.press('Escape')
    await project.waitFor({ state: 'hidden' })
    await search.clear()
    assert.equal(await cards.count(), building.summary.groups)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    await shot(page, page === mobile ? 'building-cards-mobile' : 'building-cards-desktop')
  }
  const board = await call('admin', 'GET', '/board/overview', { period: '2026-11' })
  for (const [kind, title] of [['bidding', '招标项目'], ['building', '在建项目']]) {
    const entry = board.tender.drill[kind]
    await desktop.goto(WEB + entry.path + '?' + new URLSearchParams(entry.query))
    await desktop.locator('#tender-project-list').getByRole('heading', { name: title, exact: true }).waitFor()
  }
  await desktop.goto(WEB + '/tender-projects?' + new URLSearchParams({ sheet: tender.sheet.id, row: lot.row }))
  await desktop.getByRole('dialog', { name: lot.name, exact: true }).getByRole('heading', { name: '标段明细 2' }).waitFor()
})
await step('SOURCE-task-history-dialog', '任务进展和企微投递只在过程记录中打开', async () => {
  const { items } = await call('admin', 'GET', '/operations/tasks')
  const task = items[0]
  assert.ok(task?.id && task.project_id)
  const route = `**/api/v1/operations/tasks/${task.id}`
  const timelineMarker = '验收：过程记录正文'
  const deliveryMarker = '验收：通知内容'
  const stub = async request => {
    const response = await request.fetch(), json = await response.json()
    json.data.updates = [{ id: 10, kind: 'assigned', content: timelineMarker,
      author_name: '验收用户', created_at: '2026-11-17T08:00:00+08:00' }]
    json.data.notification_deliveries = [{ id: 20, recipient: '模拟负责人',
      status: '企微已受理', title: deliveryMarker, sent_at: '2026-11-17T08:00:00+08:00' }]
    await request.fulfill({ response, json })
  }
  for (const page of [desktop, mobile]) {
    await page.route(route, stub)
    try {
      await page.goto(WEB + `/operations/${task.project_id}?task=${task.id}`)
      const current = page.getByRole('dialog', { name: '任务详情', exact: true })
      const open = current.getByRole('button', { name: '过程记录', exact: true })
      await open.waitFor()
      assert.equal(await current.getByText('企业微信提醒投递情况', { exact: true }).count(), 0)
      assert.equal(await current.getByText(timelineMarker, { exact: true }).count(), 0)
      await open.click()
      const history = page.getByRole('dialog', { name: '过程记录', exact: true })
      const delivery = history.getByText('企业微信提醒投递情况', { exact: true })
      const message = history.getByText(new RegExp(deliveryMarker))
      await history.getByText(timelineMarker, { exact: true }).waitFor()
      assert.ok(!(await message.isVisible()))
      await delivery.click()
      await message.waitFor()
      await page.keyboard.press('Escape')
      await history.waitFor({ state: 'hidden' })
      await open.click()
      await delivery.waitFor()
      assert.ok(!(await message.isVisible()), '重新打开时投递明细应收起')
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    } finally { await page.unroute(route, stub) }
  }
})
await step('SOURCE-market-filters', '商机布局、工单筛选、详情与下载只显示市场商机', async () => {
  const base = '/business-workbook/opportunities'
  const data = await call('admin', 'GET', base)
  await desktop.goto(WEB + '/opportunities')
  await desktop.getByRole('heading', { name: '商机列表', exact: true }).waitFor()
  const listHeader = desktop.locator('.opportunity-list .hd')
  assert.deepEqual(data.sheets.map(s => s.name), ['5、市场商机'])
  assert.equal(data.total, 37)
  assert.equal(await desktop.locator('.opportunity-kpis > a').count(), 3)
  for (const title of ['赢单金额', '赢单率', '商机漏斗', '业务线', '预计金额', '企业微信客户', '新增商机', '推进', '编辑']) {
    assert.equal(await desktop.getByText(title, { exact: true }).count(), 0)
  }
  await desktop.getByRole('button', { name: /^已接受\s*37/ }).click()
  await desktop.waitForURL(/status=/)
  const search = desktop.getByRole('textbox', { name: '搜索商机', exact: true })
  const row = data.items[0]
  await search.fill(row.sequence)
  await search.press('Enter')
  await listHeader.getByText('共 1 条', { exact: true }).waitFor()
  await desktop.getByRole('button', { name: row.name, exact: true }).click()
  const detail = desktop.getByRole('dialog')
  await detail.getByRole('heading', { name: '工单详情', exact: true }).waitFor()
  await detail.getByText(row.date, { exact: true }).waitFor()
  await detail.getByText(row.sequence, { exact: true }).waitFor()
  await desktop.keyboard.press('Escape')
  await detail.waitFor({ state: 'hidden' })
  const [download] = await Promise.all([desktop.waitForEvent('download'), desktop.getByRole('button', { name: '导出 xlsx' }).click()])
  assert.match(download.suggestedFilename(), /^市场商机_\d{8}\.xlsx$/)
  await desktop.getByRole('link', { name: /本月新增/ }).click()
  await listHeader.getByText(`共 ${data.stats.month.count} 条`, { exact: true }).waitFor()
  assert.equal(await desktop.getByPlaceholder('发起日期（起）').inputValue(), data.stats.month.drill.query.start)
  await desktop.getByRole('button', { name: '重置', exact: true }).click()
  await listHeader.getByText('共 37 条', { exact: true }).waitFor()
})
await step('SOURCE-no-mixing', '模块不再请求旧的混合台账', async () => { assert.deepEqual(mixedReads, []) })
await step('SOURCE-live-refresh', '页面自动发现新版本并关闭旧行明细', async () => {
  const live = await loginPage(browser, 'admin')
  await live.clock.install()
  let reads = 0
  await live.route('**/api/v1/business-workbook/opportunities?**', async route => {
    const response = await route.fetch(), json = await response.json()
    if (++reads > 1) {
      json.data.source.revision = String(Number(json.data.source.revision) + 1)
      json.data.items[0].name = '原表同步后的行'
    }
    await route.fulfill({ response, json })
  })
  await live.goto(WEB + '/opportunities')
  await live.getByRole('button', { name: '查看详情', exact: true }).first().click()
  await live.getByRole('dialog').getByRole('heading', { name: '工单详情' }).waitFor()
  await live.clock.fastForward(31000)
  await live.getByText('原表同步后的行', { exact: true }).waitFor()
  await live.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(reads, 2)
  await live.close()
})
await browser.close()
const result = save()
if (result.failed) process.exitCode = 1
