import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, call, loginPage, shot, step, save } from './lib.mjs'

const health = await fetch(API + '/health').then(r => r.json())
assert.equal(health.env, 'test', 'Use an isolated local test database')
assert.ok(['localhost', '127.0.0.1'].includes(new URL(API).hostname))
const workbook = await call('admin', 'GET', '/business-workbook/contracts')
assert.match(workbook.source.revision, /^synthetic-/, 'Use synthetic workbook records')
const users = (await call('admin', 'GET', '/admin/users', { page_size: 100 })).items
const user = id => users.find(u => u.userid === id)
const project = await call('admin', 'POST', '/operations', {
  name: '一键报警·需求验收', owner_id: user('admin').id, category: '其他运营',
  enabled_sections: ['reports', 'products', 'procurement', 'device_maintenance', 'arrears', 'collection_maintenance', 'customers', 'warehouse', 'scrap'],
})
await call('admin', 'POST', `/operations/${project.id}/members`, { user_id: user('xiekai').id, role: 'member' })
const product = await call('admin', 'POST', `/operations/${project.id}/products`, { name: '验收专用路由器' })
const school = await call('admin', 'POST', `/operations/${project.id}/schools`, { name: '需求验收学校' })
await call('admin', 'POST', `/operations/${project.id}/schools/${school.id}/equipment`, { product_id: product.id, quantity: 10, unit_price: '100.00' })
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const desktop = await loginPage(browser, 'admin')
const mobile = await loginPage(browser, 'admin', { width: 390, height: 844 })
desktop.setDefaultTimeout(12000)
mobile.setDefaultTimeout(12000)
const projectUrl = `${WEB}/operations/${project.id}`

await step('REQ-01', '一键报警六项维护及周月报入口可见', async () => {
  await desktop.goto(projectUrl)
  for (const name of ['设备运维', '欠费维护', '回款维护', '客户资料维护', '仓库物料', '设备报废', '周报', '月报'])
    await desktop.getByRole('button', { name, exact: true }).waitFor()
  await shot(desktop, 'alarm-shortcuts-desktop')
})

await step('REQ-02', '采购十台入库、实际页面出库两台后在库八台', async () => {
  await desktop.getByRole('button', { name: '仓库物料', exact: true }).click()
  await desktop.getByRole('button', { name: '登记出入库', exact: true }).click()
  const form = desktop.getByRole('dialog', { name: '登记出入库', exact: true })
  await form.getByRole('combobox', { name: '库存设备' }).click()
  await desktop.getByRole('option', { name: `验收专用路由器 · #${product.id}`, exact: true }).click()
  await form.getByRole('spinbutton', { name: '库存数量' }).fill('2')
  await form.getByRole('textbox', { name: '库存操作说明' }).fill('合成验收出库两台用于运营')
  await form.getByRole('button', { name: '确认登记', exact: true }).click()
  await form.waitFor({ state: 'hidden' })
  const stock = (await call('admin', 'GET', `/operations/${project.id}/warehouse`)).items[0]
  assert.deepEqual([stock.total, stock.warehouse, stock.operating, stock.scrapped], [10, 8, 2, 0])
  await shot(desktop, 'warehouse-eight-desktop')
})

await step('REQ-03', '报废设备后数量守恒并保留来源', async () => {
  await desktop.getByRole('button', { name: '设备报废', exact: true }).click()
  await desktop.getByRole('button', { name: '登记设备报废', exact: true }).click()
  const form = desktop.getByRole('dialog', { name: '登记设备报废', exact: true })
  await form.getByRole('combobox', { name: '库存设备' }).click()
  await desktop.getByRole('option', { name: `验收专用路由器 · #${product.id}`, exact: true }).click()
  await form.getByRole('textbox', { name: '库存操作说明' }).fill('合成验收损坏报废一台')
  await form.getByRole('button', { name: '确认登记', exact: true }).click()
  await form.waitFor({ state: 'hidden' })
  const stock = (await call('admin', 'GET', `/operations/${project.id}/warehouse`, { scrap: true })).items[0]
  assert.deepEqual([stock.total, stock.warehouse, stock.operating, stock.scrapped], [10, 7, 2, 1])
})

await step('REQ-04', '设备运维可实际录入跟进和问题', async () => {
  await desktop.getByRole('button', { name: '设备运维', exact: true }).click()
  await desktop.getByRole('button', { name: '新增设备运维', exact: true }).click()
  const form = desktop.getByRole('dialog', { name: '设备运维 · 新增记录', exact: true })
  await form.getByRole('textbox', { name: '维护事项', exact: true }).fill('报警终端巡检合成验收')
  await form.getByRole('textbox', { name: '维护客户', exact: true }).fill('合成学校')
  await form.getByRole('textbox', { name: '设备名称编号', exact: true }).fill('终端 TEST-01')
  await form.getByRole('textbox', { name: '维护处理情况', exact: true }).fill('已完成网络连通检查')
  await form.getByRole('textbox', { name: '维护存在问题', exact: true }).fill('待更换备用电池')
  await form.getByRole('textbox', { name: '维护下一步', exact: true }).fill('下次巡检复核')
  await form.getByRole('button', { name: '保存维护记录', exact: true }).click()
  await form.waitFor({ state: 'hidden' })
  await desktop.getByRole('cell', { name: '报警终端巡检合成验收', exact: true }).waitFor()
  await desktop.getByRole('button', { name: '查看 / 更新', exact: true }).first().click()
  const detail = desktop.getByRole('dialog', { name: '设备运维 · 详情与跟进', exact: true })
  await detail.getByRole('heading', { name: '过程记录', exact: true }).waitFor()
  await desktop.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity))
  await shot(desktop, 'maintenance-history-desktop')
  await detail.getByRole('button', { name: '关闭', exact: true }).click()
})

await step('REQ-05', '欠费、回款和客户资料维护按钮打开实际模块', async () => {
  for (const name of ['欠费维护', '回款维护', '客户资料维护']) {
    await desktop.getByRole('button', { name, exact: true }).click()
    await desktop.getByRole('button', { name: `新增${name}`, exact: true }).waitFor()
  }
})

await step('REQ-06', '周报与月报按钮分别打开对应周期', async () => {
  await desktop.getByRole('button', { name: '月报', exact: true }).click()
  assert.equal(await desktop.getByRole('radio', { name: '月报', exact: true }).isChecked(), true)
  await desktop.getByRole('button', { name: '周报', exact: true }).click()
  assert.equal(await desktop.getByRole('radio', { name: '周报', exact: true }).isChecked(), true)
})

await step('REQ-07', '经营业绩当月和本周下钻均可返回全年', async () => {
  await desktop.goto(WEB + '/contracts')
  for (const label of ['当月合同金额', '本周合同金额']) {
    await desktop.getByRole('button').filter({ hasText: label }).click()
    await desktop.getByRole('button', { name: '查看全年业绩', exact: true }).click()
    await desktop.waitForFunction(() => !new URL(location.href).searchParams.has('start'))
    const page = await call('admin', 'GET', '/business-workbook/contracts')
    assert.equal(page.total, 3)
  }
})

await step('REQ-08', '当月回款明细在当前页面内展开', async () => {
  await desktop.goto(WEB + '/collections')
  await desktop.locator('.quick-card').first().click()
  await desktop.getByRole('button', { name: '收起明细', exact: true }).waitFor()
  assert.equal(await desktop.getByRole('dialog').count(), 0)
  await desktop.getByRole('button', { name: '收起明细', exact: true }).click()
})

await step('REQ-09', '回款节点提醒可设置并显示超时合同', async () => {
  await desktop.getByRole('button', { name: '合成验收校园网络合同', exact: true }).first().click()
  const dialog = desktop.getByRole('dialog')
  await dialog.locator('summary').filter({ hasText: /设置负责人及日 \/ 周反馈节点|调整提醒安排/ }).click()
  await dialog.getByRole('combobox', { name: '自动提醒负责人', exact: true }).click()
  await desktop.getByRole('option', { name: user('xiekai').name, exact: true }).click()
  await dialog.getByRole('combobox', { name: '回款反馈节点日期', exact: true }).fill('2026-11-17')
  await dialog.getByRole('combobox', { name: '回款反馈节点日期', exact: true }).press('Enter')
  await dialog.getByRole('button', { name: '保存自动提醒', exact: true }).click()
  await dialog.getByText(/下次节点 2026-11-17.*已超时/).waitFor()
  await desktop.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity))
  await shot(desktop, 'collection-reminder-desktop')
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await desktop.getByRole('heading', { name: '超时未反馈合同', exact: true }).waitFor()
})

await step('REQ-10', '390px 手机页面无横向溢出，仓库与维护表单可用', async () => {
  await mobile.goto(projectUrl)
  await mobile.getByRole('button', { name: '仓库物料', exact: true }).click()
  await mobile.getByRole('cell', { name: '验收专用路由器', exact: true }).first().waitFor()
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await shot(mobile, 'warehouse-mobile')
  await mobile.getByRole('button', { name: '设备运维', exact: true }).click()
  await mobile.getByRole('button', { name: '查看 / 更新', exact: true }).first().click()
  const dialog = mobile.getByRole('dialog', { name: '设备运维 · 详情与跟进', exact: true })
  await dialog.waitFor()
  await mobile.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity))
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await shot(mobile, 'maintenance-mobile')
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await mobile.goto(WEB + '/collections')
  await mobile.getByRole('heading', { name: '超时未反馈合同', exact: true }).waitFor()
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  await shot(mobile, 'collections-mobile')
})

await step('REQ-11', '已完成的经营业绩文字提问可生成合同清单', async () => {
  await desktop.goto(WEB + '/contracts')
  await desktop.getByRole('textbox', { name: '报表提问', exact: true }).fill('查询未收款合同清单')
  await desktop.getByRole('button', { name: '生成报表', exact: true }).click()
  await desktop.locator('.report-output').getByText('合成验收校园网络合同', { exact: true }).waitFor()
  await shot(desktop, 'performance-report-desktop')
})

await step('REQ-12', '已完成的回款阶段性总结可生成并留档', async () => {
  await desktop.goto(WEB + '/collections')
  await desktop.getByRole('textbox', { name: '总结要求', exact: true }).fill('总结目前跟进情况和待反馈事项')
  await desktop.getByRole('button', { name: '生成阶段性总结', exact: true }).click()
  await desktop.getByRole('button', { name: '保存留档', exact: true }).click()
  await desktop.getByText(/已留档版本 V/).waitFor()
  await shot(desktop, 'collection-report-desktop')
})

await browser.close()
const result = save()
if (result.failed) process.exitCode = 1
