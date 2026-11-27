import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, OUT, WEB, call, loginPage, save, step } from './lib.mjs'

for (const url of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname))
const health = await fetch(API + '/health').then(response => response.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')
assert.equal(health.overrides, '')

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const administrator = await loginPage(browser, 'admin', { width: 1440, height: 1024 })
const member = await loginPage(browser, 'yangyue')
const button = (root, name) => root.getByRole('button', { name, exact: true })
const menu = (root, name) => root.getByRole('link', { name, exact: true })
const oldName = '存量项目 · 试点学校设备采购'
const newName = '新一般项目 · 交付流程验收'

async function capture(page, name) {
  await page.evaluate(() => document.fonts.ready)
  await page.locator('.el-message').first().waitFor({ state: 'hidden' })
  await page.locator('.el-loading-mask:visible').first().waitFor({ state: 'hidden' })
  const file = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, file), animations: 'disabled' })
  return file
}

async function dateField(dialog, name, value) {
  const field = dialog.getByRole('combobox', { name, exact: true })
  await field.fill(value)
  await field.press('Enter')
}

try {
  const admin = await call('admin', 'GET', '/auth/me')
  const teammate = await call('yangyue', 'GET', '/auth/me')
  assert.ok(!teammate.menus.includes('projects'))
  const previous = await call('admin', 'POST', '/projects', {
    name: oldName, business_line: 'AF', customer_name: '试点学校', status: 2, owner_id: admin.id,
    milestone_template: 'standard', start_date: '2026-11-27', budget_amount: 3600,
  })
  const code = previous.project_code
  const ledger = `/projects/${code}`
  await call('admin', 'POST', `/market/projects/${code}/team?scope=general`, { user_ids: [teammate.id] })
  const task = await call('admin', 'POST', ledger + '/tasks', {
    title: '核对交接设备', owner_id: teammate.id, preparation_on: '2026-11-27', due_on: '2026-12-09',
  })

  await step('LEDGER-01', '一般项目初始为空，项目台账列表和旧项目详情仍可打开', async () => {
    await administrator.goto(WEB + '/general-projects')
    await administrator.getByText('暂无一般项目', { exact: true }).waitFor()
    assert.equal(await administrator.getByRole('button', { name: /^查看一般项目：/ }).count(), 0)
    const empty = await capture(administrator, '01-一般项目空状态')
    await menu(administrator, '项目台账').click()
    await administrator.getByRole('heading', { name: oldName, exact: true }).click()
    await administrator.waitForURL(new RegExp(`/projects/${code}`))
    await administrator.getByRole('heading', { name: oldName, exact: true }).waitFor()
    await administrator.getByText('标准控制流程', { exact: true }).waitFor()
    const detail = await call('admin', 'GET', ledger)
    assert.equal(detail.milestones.length, 6)
    assert.equal(detail.in_general_workspace, false)
    const retained = await capture(administrator, '02-存量项目台账详情')
    return { empty, retained }
  })

  await step('LEDGER-02', '只有项目团队权限的成员可打开已分配事项，旧任务链接转到台账', async () => {
    await member.goto(WEB + '/projects')
    await menu(member, '项目台账').waitFor()
    await member.getByRole('heading', { name: oldName, exact: true }).click()
    await member.waitForURL(new RegExp(`/projects/${code}`))
    await member.getByRole('heading', { name: oldName, exact: true }).waitFor()
    assert.equal(await member.getByRole('tab', { name: '开票回款', exact: true }).count(), 0)
    await member.goto(`${WEB}/general-projects/${code}?task=${task.id}`)
    await member.waitForURL(url => url.pathname === ledger && url.searchParams.get('task') === String(task.id))
    const taskDialog = member.getByRole('dialog', { name: '任务情况', exact: true })
    await taskDialog.getByRole('heading', { name: task.title, exact: true }).waitFor()
    await button(taskDialog, '上传附件').waitFor()
  })

  await step('LEDGER-03', '从一般项目新建的记录进入一般项目总览，已有台账记录继续可查', async () => {
    await administrator.goto(WEB + '/general-projects')
    await button(administrator, '创建第一个一般项目').click()
    const form = administrator.getByRole('dialog', { name: '新建一般项目', exact: true })
    await form.getByRole('textbox', { name: '一般项目名称', exact: true }).fill(newName)
    await dateField(form, '项目准备日期', '2026-11-27')
    await dateField(form, '项目完成日期', '2026-12-15')
    await button(form, '创建一般项目').click()
    await administrator.getByRole('heading', { name: newName, exact: true }).waitFor()
    await menu(administrator, '一般项目').click()
    await button(administrator, `查看一般项目：${newName}`).waitFor()
    assert.equal(await administrator.getByRole('button', { name: /^查看一般项目：/ }).count(), 1)
    assert.equal((await call('admin', 'GET', ledger)).name, oldName)
  })
} finally {
  await browser.close()
  const { failed } = save()
  if (failed) process.exitCode = 1
}
