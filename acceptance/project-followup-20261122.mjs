import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, WEB, OUT, call, loginPage, step, save } from './lib.mjs'

for (const origin of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(origin).hostname))
assert.equal((await fetch(API + '/health').then(r => r.json())).env, 'test')
const revision = 'synthetic-followup-1122'
assert.equal((await call('admin', 'GET', '/business-workbook/contracts')).source.revision, revision)
const users = (await call('admin', 'GET', '/admin/users', { page_size: 100 })).items
const admin = users.find(u => u.userid === 'admin'), worker = users.find(u => u.userid === 'xiekai')
const op = await call('admin', 'POST', '/operations', { name: '校园服务运营验收 ' + Date.now(), owner_id: admin.id,
  enabled_sections: ['tasks', 'team', 'progress', 'reports', 'income', 'outreach'] })
const base = `/operations/${op.id}`
await call('admin', 'POST', base + '/members', { user_id: worker.id, role: 'member' })
const school = await call('admin', 'POST', base + '/schools', {
  name: '青禾实验学校', stage: 'proposal', potential: 'high', next_action: '与校务组核对交付清单', next_contact_date: '2026-11-25' })
await call('admin', 'POST', base + '/schools', { name: '明远中学', stage: 'contact', potential: 'medium' })
await call('admin', 'POST', base + '/schools', { name: '已签约示范学校', stage: 'won' })
await call('admin', 'POST', `${base}/schools/${school.id}/visits`, {
  occurred_on: '2026-11-19', content: '已完成第一轮访谈，校方希望先确认交付范围。', next_action: '与校务组核对交付清单', next_contact_date: '2026-11-25' })
await call('admin', 'POST', `${base}/schools/${school.id}/income`, { income_date: '2026-11-20', amount: '3650.50', source: '验收收款登记' })
await call('admin', 'POST', base + '/progress', { progress: '已核对首批设备，等待现场联调。', problems: '机房开放时间待确认。' })
const task = await call('admin', 'POST', base + '/tasks', { owner_id: worker.id, title: '核对学校交付范围',
  description: '核实现场安装位置并留存交付清单。', school_id: school.id, kind: 'outreach', due_date: '2026-11-26' })
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const desktop = await loginPage(browser, 'admin'), mobile = await loginPage(browser, 'xiekai', { width: 390, height: 844 })
desktop.setDefaultTimeout(9000); mobile.setDefaultTimeout(9000)
const btn = (root, name) => root.getByRole('button', { name, exact: true })
const tab = (root, name) => root.getByRole('tab', { name, exact: true })
async function snap(page, name) {
  await page.evaluate(() => document.fonts.ready)
  const file = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, file), animations: 'disabled' })
  return { evidence: file }
}
async function setDate(root, name, value) {
  const field = root.getByRole('combobox', { name, exact: true })
  await field.fill(value); await field.press('Enter')
}
async function opened(page, name) {
  const dlg = page.getByRole('dialog', { name, exact: true })
  await dlg.waitFor(); return dlg
}
async function postFeedback(page, text, screenshot) {
  const section = page.getByRole('region', { name: '项目跟踪反馈' })
  await btn(section, '填写反馈').click()
  await section.getByLabel('项目本次进展').fill(text)
  await section.getByLabel('项目下一步安排').fill('与负责人核对实施时间')
  await setDate(section, '项目下次跟进日期', '2026-11-26')
  await btn(section, '提交反馈').click()
  await section.getByText(text, { exact: true }).first().waitFor()
  return snap(page, screenshot)
}
try {
  await step('01a', '重点项目可从看板直接新建', async () => {
    await desktop.goto(WEB + '/market')
    await btn(desktop, '新建重点项目').click()
    const dialog = await opened(desktop, '新建重点项目')
    await dialog.getByLabel('重点项目名称').fill('教学楼接入改造')
    await dialog.getByLabel('重点项目内容').fill('确认现场接口、完成安装并核对交付清单。')
    await setDate(dialog, '项目完成日期', '2026-11-30')
    const result = await snap(desktop, '01a-重点项目新建')
    await btn(dialog, '创建重点项目').click()
    await desktop.waitForURL(/\/market\/projects\/JC-/)
    return result
  })
  await step('01b', '重点任务保留负责人、节点和反馈周期', async () => {
    const code = new URL(desktop.url()).pathname.split('/').pop()
    const item = await call('admin', 'POST', `/market/projects/${code}/tasks`, { title: '确认机房接口与安装位置',
      requirements: '核对网口与电源，完成现场签认。', owner_id: admin.id, preparation_on: '2026-11-22', due_on: '2026-11-28',
      cadence: 'daily', nodes: [{ name: '机房勘查', due_on: '2026-11-24' }] })
    await desktop.goto(`${WEB}/market/projects/${code}?task=${item.id}`)
    const dialog = await opened(desktop, '任务情况')
    await dialog.getByLabel('重点任务进展').fill('校方已提供机房平面图。')
    await dialog.getByLabel('重点任务下一步').fill('现场核对接口编号。')
    await btn(dialog, '提交进展').click()
    await dialog.locator('.history article').filter({ hasText: '校方已提供机房平面图。' }).waitFor()
    const detail = await call('admin', 'GET', `/market/projects/${code}/tasks/${item.id}`)
    assert.equal(detail.history.length, 1); assert.equal(detail.feedback_due_on, '2026-11-23')
    return snap(desktop, '01b-重点任务节点反馈')
  })
  await step('02', '运营留言与任务反馈进入同一条时间线', async () => {
    await desktop.goto(`${WEB}${base}?task=${task.id}`)
    const dialog = await opened(desktop, '任务详情')
    await dialog.getByLabel('要求或留言').fill('校务组已提供新版教室清单，请以该版本核对。')
    await btn(dialog, '发送讨论').click()
    await desktop.getByText('讨论已保存', { exact: true }).waitFor()
    await btn(dialog, '过程记录').click()
    const history = await opened(desktop, '过程记录')
    await history.getByText('校务组已提供新版教室清单，请以该版本核对。', { exact: true }).waitFor()
    await history.getByText('任务安排', { exact: true }).waitFor()
    assert.equal((await call('admin', 'GET', `/operations/tasks/${task.id}`)).last_feedback_at, null)
    return snap(desktop, '02-留言归入过程记录')
  })
  await step('03a', '商机可提交进展并查看过程记录', async () => {
    await desktop.goto(WEB + '/opportunities')
    await btn(desktop, '实验学校网络扩容').click()
    return postFeedback(desktop, '已收到校方对带宽方案的确认。', '03a-商机项目跟踪')
  })
  for (const [id, sheet, name, message] of [
    ['03b', 'tender-sheet', '校园设备招标', '招标文件已完成内部复核。'],
    ['03c', 'pending', '信息教室设备安装', '设备到场清点已完成。'],
  ]) {
    await step(id, `${name}可持续反馈`, async () => {
      await desktop.goto(`${WEB}/tender-projects?sheet=${sheet}`)
      await btn(desktop, `查看项目：${name}`).click()
      return postFeedback(desktop, message, `${id}-${name}跟踪`)
    })
  }
  await step('03d', '一般项目可记录问题和下次跟进安排', async () => {
    const project = (await call('admin', 'GET', '/projects', { scope: 'general' })).items[0]
    await desktop.goto(`${WEB}/projects/${project.project_code}`)
    const panel = desktop.locator('section.panel').filter({ has: desktop.getByRole('heading', { name: '项目跟踪反馈' }) })
    await panel.getByPlaceholder('填写今日进展、重点事件或风险').fill('已核对本期交付记录。')
    await panel.getByLabel('项目下一步安排').fill('补齐现场签认页。')
    await setDate(panel, '项目下次跟进日期', '2026-11-25')
    await btn(panel, '提交').click()
    await panel.locator('.feed').getByText(/补齐现场签认页。.*2026-11-25/s).first().waitFor()
    await panel.scrollIntoViewIfNeeded()
    return snap(desktop, '03d-一般项目跟踪')
  })
  await step('03e', '经营业绩合同可直接进入同一份回款执行记录', async () => {
    await desktop.goto(`${WEB}/contracts/items/sales-sheet/3?revision=${revision}`)
    await btn(desktop, '回款跟踪反馈').click()
    await desktop.getByRole('heading', { name: '执行情况反馈', exact: true }).waitFor()
    return snap(desktop, '03e-合同执行跟踪')
  })
  await step('04', '运营综合汇报按期间核对营收、项目进度与任务', async () => {
    await desktop.goto(WEB + base)
    await tab(desktop, '周报 / 月报').click()
    await desktop.getByLabel('汇总周次').fill('2026-W47')
    await btn(desktop, '查看汇总').click()
    const report = desktop.locator('.digest-text')
    await report.filter({ hasText: '3,650.50 元' }).waitFor()
    assert.match(await report.innerText(), /已核对首批设备，等待现场联调/); assert.match(await report.innerText(), /核对学校交付范围/)
    await btn(desktop, '生成并保存本期报告').click()
    await desktop.getByText(/已保存版本 1/).waitFor()
    await report.scrollIntoViewIfNeeded()
    return snap(desktop, '04-运营综合汇报')
  })
  await step('07', '回款首页默认显示本月实际到账，可切换未收及预估', async () => {
    await desktop.goto(WEB + '/collections')
    const list = desktop.locator('#collection-list')
    await list.getByRole('heading', { name: '当月回款清单' }).waitFor()
    assert.match(await list.innerText(), /25,000.00/); assert.equal(await list.locator('tbody tr').count(), 3)
    const result = await snap(desktop, '07a-当月回款与预估')
    await list.scrollIntoViewIfNeeded()
    await list.screenshot({ path: path.join(OUT, 'screens/07b-当月实际到账清单.png'), animations: 'disabled' })
    await btn(desktop, '查看未收合同').click()
    await list.getByRole('heading', { name: '未收项目清单' }).waitFor()
    assert.equal(await list.getByText('教学终端维保合同', { exact: true }).count(), 0)
    return result
  })
  await step('06', '到期未收产生实际提醒，负责人必须汇报未收原因', async () => {
    const itemPath = '/business-workbook/collections/items/sales-sheet/3'
    const previous = (await call('admin', 'GET', itemPath + '/execution')).reminder.rule
    const rule = (await call('admin', 'PUT', itemPath + '/reminder', { revision, recipient_id: worker.id,
      manager_ids: [admin.id], due_on: '2026-11-29', payment_due_on: '2026-11-22', cadence: 'weekly', expected_revision: previous?.revision || 0 })).rule
    const job = await call('admin', 'POST', '/admin/jobs/collection_feedback_reminders/run', { force: true })
    assert.equal(job.error || '', '')
    assert.ok((await call('xiekai', 'GET', '/notifications', { page_size: 100 })).items.some(n => n.link === `/collections/followups/${rule.request_id}` && n.title.includes('到期')))
    await mobile.goto(`${WEB}/collections/followups/${rule.request_id}`)
    await mobile.getByLabel('回复跟进情况').fill('已与客户财务核对付款进度。')
    const submit = mobile.getByRole('button', { name: /^(回复跟进情况|提交跟进记录)$/ })
    assert.ok(await submit.isDisabled())
    await mobile.getByLabel('存在问题', { exact: true }).fill('客户仍在核验签收资料。')
    await mobile.getByLabel('下一步安排', { exact: true }).fill('周一补交签收单并复核。')
    await submit.click()
    const record = mobile.locator('.el-timeline').getByText('存在问题：客户仍在核验签收资料。', { exact: true }).last()
    await record.waitFor(); await record.scrollIntoViewIfNeeded()
    await snap(mobile, '06b-手机提交未收原因')
    await desktop.goto(WEB + '/collections')
    await desktop.getByText('到期未收 · 今日已反馈', { exact: true }).waitFor()
    await desktop.getByRole('heading', { name: '到期未收与反馈提醒' }).scrollIntoViewIfNeeded()
    return snap(desktop, '06a-合同到期提醒')
  })
  await step('05', '小聚用同一份合同数据逐笔汇报回款及原因', async () => {
    await desktop.goto(WEB + '/collections')
    await btn(desktop, '小聚逐笔汇报').click()
    await desktop.waitForURL(/\/xiaoju/)
    const answer = desktop.locator('.xj').getByText(/全部合同当月实收 25,000.00 元/).last()
    await answer.waitFor()
    const body = await desktop.locator('.xj').innerText()
    for (const value of ['校园网络服务合同', '客户仍在核验签收资料', '教学终端维保合同', '校园广播升级合同', '校园视频系统尾款']) assert.ok(body.includes(value), value)
    const scroll = desktop.locator('.scroll')
    if (await scroll.count()) await scroll.evaluate(el => el.scrollTo({ top: 0, behavior: 'instant' }))
    return snap(desktop, '05-小聚逐笔回款汇报')
  })
  await step('08', '手机待开拓学校卡片显示责任、实际进展和跟进日期', async () => {
    await mobile.goto(WEB + base)
    await tab(mobile, '市场开拓').click()
    const cards = mobile.getByLabel('待开拓学校任务卡')
    const card = btn(cards, '查看学校跟进：青禾实验学校')
    await card.waitFor()
    assert.match(await card.innerText(), /校方希望先确认交付范围/); assert.match(await card.innerText(), /2026-11-25/)
    assert.equal(await cards.getByText('已签约示范学校').count(), 0)
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    await card.scrollIntoViewIfNeeded()
    const result = await snap(mobile, '08-待开拓学校任务卡')
    await card.click()
    await mobile.getByRole('heading', { name: '实际跟进记录' }).waitFor()
    return result
  })
} finally { await browser.close() }
if (save().failed) process.exitCode = 1
