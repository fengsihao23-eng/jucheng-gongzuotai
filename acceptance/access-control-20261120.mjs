import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, call, loginPage, shot, step, save, token } from './lib.mjs'

assert.equal((await fetch(API + '/health').then(r => r.json())).env, 'test')
assert.ok(['localhost', '127.0.0.1'].includes(new URL(API).hostname), 'Only use isolated local fixtures')
const users = (await call('admin', 'GET', '/admin/users', { page_size: 100 })).items
const worker = users.find(row => row.userid === 'xiekai'), owner = users.find(row => row.userid === 'wujun')
const original = await call('admin', 'GET', `/admin/users/${worker.id}/effective-permissions`)
const project = await call('admin', 'POST', '/operations', {
  name: '权限浏览器验收 ' + Date.now(), owner_id: owner.id, enabled_sections: ['tasks', 'team', 'products', 'progress', 'income', 'reports'],
})
await call('admin', 'POST', `/operations/${project.id}/members`, { user_id: worker.id, role: 'member' })
const task = await call('admin', 'POST', `/operations/${project.id}/tasks`, {
  title: '右键删除密码验收', owner_id: owner.id, due_date: '2026-10-30',
})
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const admin = await loginPage(browser, 'admin'), member = await loginPage(browser, 'xiekai')
const superuser = await loginPage(browser, 'SZGLY')
for (const page of [admin, member, superuser]) page.setDefaultTimeout(12000)
let permissions
const checkboxLabel = input => input.locator('xpath=ancestor::label[1]')
async function setChecked(input, value) {
  if (await input.isChecked() !== value) await checkboxLabel(input).click()
}

try {
  await step('ACCESS-01', '任务卡右键删除，错误密码保留原任务，正确密码删除并保留历史', async () => {
    await admin.goto(`${WEB}/operations/${project.id}`)
    const card = admin.locator('.task-card').filter({ hasText: task.title })
    await card.click({ button: 'right' })
    await admin.getByRole('menuitem', { name: '删除任务', exact: true }).click()
    const dialog = admin.getByRole('dialog', { name: '删除任务', exact: true })
    await dialog.getByPlaceholder('请输入删除密码').fill('wrong')
    await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
    await dialog.getByText('删除密码不正确，请重新输入', { exact: true }).waitFor()
    assert.equal((await call('admin', 'GET', `/operations/tasks/${task.id}`)).id, task.id)
    await shot(admin, 'task-delete-wrong-password')
    await dialog.getByPlaceholder('请输入删除密码').fill('admin123')
    await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
    await dialog.waitFor({ state: 'hidden' })
    await card.waitFor({ state: 'detached' })
    assert.ok((await call('admin', 'GET', `/operations/tasks/${task.id}`, { archived: true })).archived_at)
  })

  await step('ACCESS-02', '运营项目右键删除也必须经过服务端密码校验', async () => {
    const disposable = await call('admin', 'POST', '/operations', { name: '删除项目密码验收 ' + Date.now(), owner_id: owner.id })
    await superuser.goto(WEB + '/operations')
    const card = superuser.locator('.project-card').filter({ hasText: disposable.name })
    await card.click({ button: 'right' })
    await superuser.getByRole('button', { name: '删除项目', exact: true }).click()
    const dialog = superuser.getByRole('dialog', { name: '删除运营项目', exact: true })
    await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
    await dialog.getByText('请输入删除密码', { exact: true }).waitFor()
    await dialog.getByPlaceholder('请输入删除密码').fill('wrong')
    await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
    await dialog.getByText('删除密码不正确，请重新输入', { exact: true }).waitFor()
    assert.equal((await call('SZGLY', 'GET', `/operations/${disposable.id}`)).id, disposable.id)
    await dialog.getByPlaceholder('请输入删除密码').fill('admin123')
    await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
    await dialog.waitFor({ state: 'hidden' })
    await card.waitFor({ state: 'detached' })
  })

  await step('ACCESS-03', '个人权限按大项分组，可全选、取消和单选小项，保存内容与接口一致', async () => {
    await admin.goto(WEB + '/admin')
    await admin.locator('.el-select').filter({ has: admin.getByRole('combobox', { name: '人员来源', exact: true }) }).locator('.el-select__wrapper').click()
    await admin.getByRole('option', { name: '全部账号', exact: true }).click()
    await admin.locator('.account-details summary').click()
    const search = admin.getByRole('textbox', { name: '搜索账号', exact: true })
    await search.fill(worker.userid); await search.press('Tab')
    await admin.locator('.account-details').getByRole('button', { name: worker.name, exact: true }).click()
    permissions = admin.getByRole('dialog', { name: '个人有效权限', exact: true })
    await permissions.locator('.el-radio-button').filter({ hasText: '单独配置' }).click()
    const group = permissions.locator('.permission-group').filter({ has: admin.locator('.group-heading').filter({ hasText: '运营项目' }) })
    const all = group.getByRole('checkbox', { name: '运营项目全选', exact: true })
    await setChecked(all, true)
    assert.equal(await group.getByRole('checkbox', { name: '仓库物料', exact: true }).isChecked(), true)
    await setChecked(group.getByRole('checkbox', { name: '收入情况', exact: true }), false)
    await checkboxLabel(all).locator('.is-indeterminate').waitFor()
    await checkboxLabel(all).click()
    await checkboxLabel(all).click()
    await setChecked(group.getByRole('checkbox', { name: '运营项目', exact: true }), true)
    await setChecked(group.getByRole('checkbox', { name: '任务情况 / 月度任务 / 当前待办', exact: true }), true)
    await setChecked(group.getByRole('checkbox', { name: '设备产品库', exact: true }), true)
    await permissions.getByRole('button', { name: '保存此人权限', exact: true }).click()
    await permissions.getByRole('status').filter({ hasText: '已保存' }).waitFor()
    const actual = await call('admin', 'GET', `/admin/users/${worker.id}/effective-permissions`)
    assert.deepEqual(actual.sections.operations, ['products', 'tasks'])
    assert.equal(actual.workbench_enabled, true)
    await admin.locator('.el-message').last().waitFor({ state: 'hidden' })
    await shot(admin, 'permission-groups-desktop')
  })

  await step('ACCESS-menu-parity', '个人配置列出整个侧栏，共用权限保存后与实际可见入口一致', async () => {
    const checkbox = name => permissions.getByRole('checkbox', { name, exact: true })
    const labels = async locator => (await locator.allTextContents()).map(label => label.trim())
    const configuredEntries = permissions.locator('.group-heading > .el-checkbox:first-child .el-checkbox__label')
    assert.deepEqual(await labels(configuredEntries), await labels(admin.locator('.side a.item')))

    const contracts = checkbox('经营业绩'), approvals = checkbox('审批台账'), collections = checkbox('回款情况')
    const invoices = checkbox('开票回款'), projectGroup = ['招标与在建', '一般项目', '历史项目'].map(checkbox)
    await setChecked(invoices, false)
    await setChecked(contracts, false)
    assert.ok(await collections.isDisabled())
    assert.equal(await collections.isChecked(), false)
    await setChecked(invoices, true)
    assert.equal(await collections.isChecked(), true)
    assert.equal(await contracts.isChecked(), false)
    await setChecked(invoices, false)
    await setChecked(approvals, true)
    assert.equal(await contracts.isChecked(), true)
    assert.equal(await collections.isChecked(), true)
    assert.equal(await invoices.isChecked(), false)
    await setChecked(checkbox('导出合同'), true)
    await setChecked(approvals, false)
    assert.equal(await checkbox('导出合同').isChecked(), false)
    await setChecked(approvals, true)

    await setChecked(projectGroup[2], false)
    for (const box of projectGroup) assert.equal(await box.isChecked(), false)
    await setChecked(projectGroup[1], true)
    for (const box of projectGroup) assert.equal(await box.isChecked(), true)

    const profile = `/admin/users/${worker.id}/effective-permissions`
    const saved = admin.waitForResponse(response => response.request().method() === 'PUT' &&
      new URL(response.url()).pathname.endsWith(`/admin/users/${worker.id}/permissions`))
    await permissions.getByRole('button', { name: '保存此人权限', exact: true }).click()
    assert.ok((await saved).ok())
    await permissions.getByRole('status').filter({ hasText: '已保存' }).waitFor()
    const actual = await call('admin', 'GET', profile)
    assert.ok(actual.menu_keys.includes('contracts') && actual.menu_keys.includes('projects'))
    assert.ok(!actual.menu_keys.includes('receivables'))
    await member.goto(WEB + '/')
    await member.locator('.side a.item').filter({ hasText: '经营业绩' }).waitFor()
    assert.deepEqual(await labels(permissions.locator('.effective-saved .jc-pill')), await labels(member.locator('.side a.item')))
    await shot(admin, 'permission-workbench-menu-parity')
  })

  await step('ACCESS-04', '成员已有登录态读取真实小项权限，未授权页签及接口不可见', async () => {
    await member.goto(`${WEB}/operations/${project.id}`)
    await member.getByRole('tab', { name: '设备产品库', exact: true }).waitFor()
    assert.deepEqual((await member.getByRole('tab').allTextContents()).sort(), ['任务情况', '设备产品库'].sort())
    assert.equal((await call('xiekai', 'GET', `/operations/${project.id}/income-board`, undefined, { raw: true })).status, 403)
    await shot(member, 'member-only-authorized-tabs')
  })

  await step('ACCESS-05', '关闭工作台后旧会话立即被拦截，重新单独开通后同一会话恢复', async () => {
    const access = permissions.getByRole('checkbox', { name: '允许使用工作台', exact: true })
    await setChecked(access, false)
    await permissions.getByRole('button', { name: '保存此人权限', exact: true }).click()
    await permissions.getByText('当前账号无法查看和使用工作台，需要单独重新配置权限。', { exact: true }).waitFor()
    assert.equal((await call('xiekai', 'GET', '/operations', undefined, { raw: true })).status, 403)
    const oldToken = await member.evaluate(() => localStorage.getItem('jc_token'))
    await member.reload()
    await member.waitForURL('**/403')
    await member.getByText('当前账号无法查看和使用工作台。请联系管理员单独重新配置权限。', { exact: true }).waitFor()
    assert.equal(await member.locator('.side').count(), 0)
    await shot(member, 'workbench-disabled')
    await setChecked(access, true)
    await permissions.getByRole('button', { name: '保存此人权限', exact: true }).click()
    await permissions.getByRole('status').filter({ hasText: '已保存' }).waitFor()
    await member.getByRole('button', { name: '重新检查权限', exact: true }).click()
    await member.waitForURL(WEB + '/')
    await member.goto(`${WEB}/operations/${project.id}`)
    await member.getByRole('tab', { name: '设备产品库', exact: true }).waitFor()
    assert.equal(await member.evaluate(() => localStorage.getItem('jc_token')), oldToken)
  })

  await step('ACCESS-06', '从团队移除后项目详情、列表均失权，三位最高权限人员仍可访问', async () => {
    await call('admin', 'DELETE', `/operations/${project.id}/members/${worker.id}`,
      { password: process.env.OPERATIONS_DELETE_PASSWORD || 'admin123' }, { deleteJSON: true })
    const listing = await call('xiekai', 'GET', '/operations')
    assert.ok(!listing.items.some(row => row.id === project.id))
    assert.equal((await call('xiekai', 'GET', `/operations/${project.id}`, undefined, { raw: true })).status, 404)
    for (const userid of ['SZGLY', 'ken', 'FengSiHao']) {
      assert.equal((await call(userid, 'GET', `/operations/${project.id}`)).id, project.id)
    }
  })

  await step('ACCESS-07', '手机宽度下个人授权卡片、勾选和保存入口均可使用', async () => {
    await admin.setViewportSize({ width: 390, height: 844 })
    await permissions.getByRole('button', { name: '保存此人权限', exact: true }).waitFor()
    await admin.locator('.el-message').last().waitFor({ state: 'hidden' })
    await shot(admin, 'permission-groups-mobile')
    const overflow = await admin.evaluate(() => [...document.querySelectorAll('body *')].map(el => ({
      tag: el.tagName, cls: String(el.className).slice(0, 100), left: el.getBoundingClientRect().left,
      right: el.getBoundingClientRect().right,
    })).filter(el => el.right > innerWidth + 1 && el.right > el.left).slice(0, 12))
    assert.ok(await admin.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), JSON.stringify(overflow))
  })
} finally {
  const current = await call('admin', 'GET', `/admin/users/${worker.id}/effective-permissions`)
  await call('admin', 'PUT', `/admin/users/${worker.id}/permissions`, {
    role_codes: original.assigned_roles, permissions: original.individual_permissions,
    workbench_enabled: original.workbench_enabled, expected_revision: current.permission_revision,
  })
  await browser.close()
}
const result = save()
process.exitCode = result.failed ? 1 : 0
