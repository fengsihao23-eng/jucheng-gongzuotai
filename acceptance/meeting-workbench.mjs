import { chromium } from 'playwright'
import { API, WEB, call, loginPage, save, shot, step } from './lib.mjs'

const health = await fetch(API + '/health').then(r => r.json())
if (health.env !== 'test') throw new Error('此脚本只可在独立测试环境运行')
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const users = (await call('admin', 'GET', '/admin/users')).items
const admin = users.find(x => x.userid === 'admin')
const assignee = users.find(x => x.userid === 'xuhao')
const project = await call('admin', 'POST', '/operations', { name: '会议试点验收', owner_id: admin.id })
const school = await call('admin', 'POST', '/operations/' + project.id + '/schools', { name: '甲校' })
await call('admin', 'POST', '/operations/' + project.id + '/members', { user_id: assignee.id, role: 'co_owner' })
const task = await call('admin', 'POST', '/operations/' + project.id + '/tasks', {
  title: '甲校确认建设需求', owner_id: assignee.id, due_date: '2026-10-20', school_id: school.id, kind: 'outreach',
  visibility: 'selected', nodes: [{ name: '确认需求', due_date: '2026-10-20' }],
})
const page = await loginPage(browser, 'admin')
const waitFor = async (locator) => locator.waitFor({ state: 'visible', timeout: 10000 })

await step('A01', '角色人数下钻及实名权限', async () => {
  await page.goto(WEB + '/admin')
  const row = page.getByRole('row').filter({ has: page.getByText('ADMIN-01', { exact: true }) })
  await row.getByRole('button').first().click()
  const drawer = page.locator('.el-drawer').filter({ hasText: '角色人员' })
  await waitFor(drawer.getByText('admin', { exact: true }))
  await drawer.getByRole('button', { name: '系统管理员', exact: true }).or(drawer.getByRole('button', { name: admin.name, exact: true })).first().click()
  await waitFor(page.getByText('个人有效权限', { exact: true }))
  await waitFor(page.getByText('运营项目：可管理全量', { exact: false }))
})

await step('D02', '签约卡片下钻到同页清单', async () => {
  await page.goto(WEB + '/contracts')
  await page.getByRole('button', { name: /本周签约/ }).click()
  await page.waitForURL(/sign_period=week/)
  await waitFor(page.getByText('签订日期', { exact: true }))
  return await shot(page, 'signing')
})

await step('D03', '回款分组及跟进抽屉', async () => {
  await page.goto(WEB + '/collections')
  await waitFor(page.getByRole('button', { name: /2026 年以前/ }))
  await page.getByRole('button', { name: '跟进', exact: true }).first().click()
  await waitFor(page.getByText('跟进记录', { exact: true }))
  await waitFor(page.getByText('催办', { exact: true }))
})

await step('B04', '学校入口与文件核对', async () => {
  await page.goto(WEB + '/operations/' + project.id)
  await page.getByRole('tab', { name: '学校采购' }).click()
  await waitFor(page.getByRole('button', { name: '查看采购' }))
  await page.getByRole('tab', { name: '每日收入' }).click()
  await page.getByRole('button', { name: '按校导入每日收入' }).click()
  await page.locator('input[type=file]').setInputFiles({
    name: '收入样表.csv', mimeType: 'text/csv', buffer: Buffer.from('学校,实际收款日期,收入金额,备注\n甲校,2026-10-19,21.25,验收\n'),
  })
  await page.getByRole('button', { name: '读取并核对' }).click()
  await waitFor(page.getByText(/拟新增/))
  await page.getByRole('button', { name: '确认新增' }).click()
  await waitFor(page.getByText('每日收入已登记', { exact: true }))
})

await step('B02', '手机端按节点反馈', async () => {
  const mobile = await loginPage(browser, 'xuhao', { width: 390, height: 844 })
  await mobile.goto(WEB + '/operations/' + project.id + '?task=' + task.id)
  await waitFor(mobile.getByRole('heading', { name: task.title, exact: true }))
  const dlg = mobile.getByRole('dialog', { name: '任务详情' })
  await dlg.getByText('已完成', { exact: true }).first().click()
  await dlg.getByPlaceholder('写几句话即可', { exact: false }).fill('校方已确认需求')
  await dlg.locator('label.el-checkbox').filter({ hasText: '确认需求' }).click()
  await dlg.getByRole('button', { name: '提交进展' }).click()
  await waitFor(dlg.getByText(/校方已确认需求/))
  return await shot(mobile, 'mobile-task')
})

await step('C03', '招标、一般及历史项目入口', async () => {
  for (const [path, title] of [['/tender-projects', '招标与在建'], ['/general-projects', '一般项目'], ['/history-projects', '历史项目']]) {
    await page.goto(WEB + path)
    await waitFor(page.getByRole('heading', { name: title, exact: true }))
  }
})

await browser.close()
const result = save()
if (result.failed) process.exitCode = 1
