import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, OUT, WEB, call, loginPage, save, shot, step } from './lib.mjs'

assert.ok(['localhost', '127.0.0.1'].includes(new URL(API).hostname))
assert.equal((await fetch(API + '/health').then(r => r.json())).env, 'test')
const options = await call('admin', 'GET', '/operations/options')
const keys = options.features.map(option => option.value)
const users = (await call('admin', 'GET', '/admin/users', { page_size: 100 })).items
const member = users.find(row => row.userid === 'xiekai')
const permissionsPath = `/admin/users/${member.id}`
const original = await call('admin', 'GET', permissionsPath + '/effective-permissions')
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const admin = await loginPage(browser, 'admin', { width: 1440, height: 1080 })
const worker = await loginPage(browser, member.userid)
const mobile = await loginPage(browser, 'admin', { width: 390, height: 844 })
for (const page of [admin, worker, mobile]) page.setDefaultTimeout(12000)

let projectId
const name = '运营项目 · 功能配置示例'
const education = '明德实验学校（示例）'
const other = '启程产业园（示例）'
const checkboxLabel = box => box.locator('xpath=ancestor::label[1]')
const toggle = box => checkboxLabel(box).click()
const chooseCategory = (page, label) => page.getByRole('region', { name: '项目用户清单' })
  .getByRole('radio', { name: label, exact: true }).locator('xpath=ancestor::label[1]').click()
const tab = (page, label) => page.getByRole('tab', { name: label, exact: true })
const projectUrl = () => `/operations/${projectId}`
const groupBox = (dialog, label) => dialog.getByRole('checkbox', { name: label, exact: true })
async function screenshot(locator, name) {
  const filename = `screens/${name}.png`
  await locator.screenshot({ path: path.join(OUT, filename), animations: 'disabled' })
  return filename
}

try {
  await step('OPS-01', '新建及编辑可配置全部 18 项标准功能，任务和设备产品库可独立关闭', async () => {
    await admin.goto(WEB + '/operations')
    await admin.getByRole('button', { name: '新建运营项目', exact: true }).click()
    const create = admin.getByRole('dialog', { name: '新建运营项目', exact: true })
    await create.getByRole('textbox', { name: '项目名称', exact: true }).fill(name)
    const all = groupBox(create, '全选')
    await toggle(all)
    const checkboxes = await create.getByRole('group', { name: '标准功能', exact: true }).getByRole('checkbox').all()
    assert.equal(checkboxes.length, keys.length)
    for (const box of checkboxes) assert.ok(await box.isEnabled() && await box.isChecked())
    const response = admin.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/v1/operations'))
    await create.getByRole('button', { name: '保存项目', exact: true }).click()
    const created = (await (await response).json()).data
    assert.deepEqual(created.feature_settings.sections, keys)
    projectId = created.id
    await admin.waitForURL(WEB + projectUrl())
    await tab(admin, '用户清单').waitFor()
    await admin.getByRole('button', { name: '编辑项目', exact: true }).click()
    const edit = admin.getByRole('dialog', { name: '编辑运营项目', exact: true })
    await checkboxLabel(groupBox(edit, '工作设置')).waitFor()
    const evidence = await screenshot(edit.locator('.feature-box'), '01-project-features')
    for (const label of ['任务情况', '设备产品库']) await toggle(groupBox(edit, label))
    await edit.getByRole('button', { name: '保存项目', exact: true }).click()
    await tab(admin, '任务情况').waitFor({ state: 'detached' })
    assert.equal(await tab(admin, '设备产品库').count(), 0)
    assert.equal(await tab(admin, '学校采购').count(), 1)
    assert.equal(await tab(admin, '仓库物料').count(), 1)
    const features = (await call('admin', 'GET', projectUrl())).feature_settings.sections
    assert.deepEqual(features, keys.filter(k => k !== 'tasks' && k !== 'products'))
    await call('admin', 'POST', projectUrl() + '/members', { user_id: member.id, role: 'member' })
    return { evidence, features: keys.length }
  })

  await step('OPS-02', '用户清单的教育类和其他能新增、分别显示、检索并在刷新后保留', async () => {
    await tab(admin, '用户清单').click()
    for (const [label, row, contact] of [['教育类', education, '示例老师'], ['其他', other, '示例园区负责人']]) {
      await chooseCategory(admin, label)
      await admin.getByRole('button', { name: '新增用户', exact: true }).click()
      const editor = admin.getByRole('dialog', { name: '新增清单用户', exact: true })
      await editor.getByRole('textbox', { name: '用户或单位名称', exact: true }).fill(row)
      await editor.getByRole('textbox', { name: '联系人', exact: true }).fill(contact)
      await editor.getByRole('textbox', { name: '备注', exact: true }).fill('仅供界面验收的示例资料')
      await editor.getByRole('button', { name: '保存用户', exact: true }).click()
      await admin.getByRole('cell', { name: row, exact: true }).waitFor()
    }
    await admin.reload()
    await tab(admin, '用户清单').click()
    await admin.getByRole('cell', { name: education, exact: true }).waitFor()
    assert.equal(await admin.getByRole('cell', { name: other, exact: true }).count(), 0)
    const evidence = [await shot(admin, '02-user-list-education')]
    await chooseCategory(admin, '其他')
    await admin.getByRole('cell', { name: other, exact: true }).waitFor()
    evidence.push(await shot(admin, '03-user-list-other'))
    await admin.getByRole('textbox', { name: '搜索用户或联系人', exact: true }).fill('教育')
    await admin.getByText('未找到匹配的用户', { exact: true }).waitFor()
    return { evidence }
  })

  await step('OPS-03', '运营项目全套授权及共享入口都可见，取消与恢复用户清单授权立即生效', async () => {
    await admin.goto(WEB + '/admin')
    await admin.locator('.el-select').filter({ has: admin.getByRole('combobox', { name: '人员来源', exact: true }) }).locator('.el-select__wrapper').click()
    await admin.getByRole('option', { name: '全部账号', exact: true }).click()
    await admin.locator('.account-details summary').click()
    const search = admin.getByRole('textbox', { name: '搜索账号', exact: true })
    await search.fill(member.userid)
    await search.press('Tab')
    await admin.locator('.account-details').getByRole('button', { name: member.name, exact: true }).click()
    const dialog = admin.getByRole('dialog', { name: '个人有效权限', exact: true })
    await dialog.locator('.el-radio-button').filter({ hasText: '单独配置' }).click()
    const ops = dialog.locator('.permission-group').filter({ has: admin.getByRole('checkbox', { name: '运营项目', exact: true }) })
    const userList = groupBox(ops, '用户清单')
    await checkboxLabel(userList).waitFor()
    assert.equal(await ops.locator('.group-options:not(.actions) input[type=checkbox]').count(), keys.length)
    for (const label of ['经营大屏', '项目台账', '零星回款', '企业微信接口', '消息通知', '个人中心', '小聚']) {
      assert.equal(await groupBox(dialog, label).count(), 1, label)
    }
    assert.ok(await groupBox(dialog, '企业微信接口').isDisabled())
    if (!await userList.isChecked()) await toggle(userList)
    const evidence = await screenshot(ops, '04-personal-operations-permissions')
    await toggle(userList)
    await dialog.getByRole('button', { name: '保存此人权限', exact: true }).click()
    await dialog.getByRole('status').filter({ hasText: '已保存' }).waitFor()
    assert.equal((await call(member.userid, 'GET', projectUrl() + '/user-list', { category: 'education' }, { raw: true })).status, 403)
    await worker.goto(WEB + projectUrl())
    await tab(worker, '市场开拓').waitFor()
    assert.equal(await tab(worker, '用户清单').count(), 0)

    await toggle(userList)
    await dialog.getByRole('button', { name: '保存此人权限', exact: true }).click()
    await dialog.getByRole('status').filter({ hasText: '已保存' }).waitFor()
    await worker.reload()
    await tab(worker, '用户清单').click()
    await worker.getByRole('cell', { name: education, exact: true }).waitFor()
    assert.equal(await worker.getByRole('button', { name: '新增用户', exact: true }).count(), 0)
    return { evidence }
  })

  await step('OPS-04', '手机端可切换用户类别且整页无横向溢出', async () => {
    await mobile.goto(WEB + projectUrl())
    await tab(mobile, '用户清单').click()
    await chooseCategory(mobile, '其他')
    await mobile.getByRole('cell', { name: other, exact: true }).waitFor()
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    return { evidence: await shot(mobile, '05-user-list-mobile') }
  })
} finally {
  const current = await call('admin', 'GET', permissionsPath + '/effective-permissions')
  await call('admin', 'PUT', permissionsPath + '/permissions', {
    role_codes: original.assigned_roles, workbench_enabled: original.workbench_enabled,
    permissions: original.individual_permissions, expected_revision: current.permission_revision,
  })
  await browser.close()
}

if (save().failed) process.exitCode = 1
