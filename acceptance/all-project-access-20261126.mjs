import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, WEB, OUT, call, loginPage, save, step } from './lib.mjs'

for (const url of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname))
const health = await fetch(API + '/health').then(response => response.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')
assert.equal(health.overrides, '')

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const manager = await call('admin', 'GET', '/auth/me')
const account = await call('chenjing', 'GET', '/auth/me')
const endpoint = `/admin/users/${account.id}`
const before = await call('admin', 'GET', endpoint + '/effective-permissions')
const admin = await loginPage(browser, 'admin', { width: 1440, height: 1000 })
const viewer = await loginPage(browser, 'chenjing', { width: 1440, height: 1000 })
const permissions = admin.getByRole('dialog', { name: '个人有效权限', exact: true })
const switchLabel = '允许查看所有项目'
const toggle = permissions.getByRole('checkbox', { name: switchLabel, exact: true })
const saveButton = permissions.getByRole('button', { name: '保存此人权限', exact: true })

async function openPermissions() {
  await admin.goto(WEB + '/admin')
  await admin.getByRole('button', { name: /^财务部，/ }).click()
  await admin.getByRole('dialog', { name: '财务部 · 部门人员' })
    .getByRole('button', { name: `查看 ${account.name} 的权限`, exact: true }).click()
  await permissions.getByRole('heading', { name: account.name, exact: true }).waitFor()
}

async function saveSwitch(value) {
  await permissions.locator('label.el-checkbox').filter({ hasText: switchLabel }).click()
  assert.equal(await toggle.isChecked(), value)
  const saved = admin.waitForResponse(response => response.url().endsWith(endpoint + '/permissions') &&
    response.request().method() === 'PUT' && response.status() === 200)
  await saveButton.click()
  await saved
  await permissions.getByRole('status').filter({ hasText: `已保存 ${account.name} 的权限` }).waitFor()
  assert.equal((await call('admin', 'GET', endpoint + '/effective-permissions')).all_project_access, value)
}

try {
  assert.equal(before.all_project_access, false)
  const plan = { owner_id: manager.id, preparation_on: '2026-11-20', due_on: '2026-12-10' }
  const key = await call('admin', 'POST', '/market/projects', { ...plan, name: '团队外查看验收重点项目' })
  const general = await call('admin', 'POST', '/market/projects?scope=general', { ...plan, name: '团队外查看验收一般项目' })
  const ops = await call('admin', 'POST', '/operations', { owner_id: manager.id, name: '团队外查看验收运营项目' })
  const task = await call('admin', 'POST', `/operations/${ops.id}/tasks`, {
    title: '核对范围权限', owner_id: manager.id, due_date: '2026-12-09',
  })
  const projects = [
    { page: `/operations/${ops.id}`, api: `/operations/${ops.id}`, title: '团队外查看验收运营项目' },
    { page: `/market/projects/${key.project_code}`, api: `/market/projects/${key.project_code}`, title: '团队外查看验收重点项目' },
    { page: `/general-projects/${general.project_code}`, api: `/market/projects/${general.project_code}?scope=general`, title: '团队外查看验收一般项目' },
  ]
  await step('SCOPE-01', '全公司数据范围的非成员默认无项目访问，系统管理可保存个人开关', async () => {
    assert.equal(account.data_scope, 'all')
    for (const project of projects) {
      const denied = await call('chenjing', 'GET', project.api, undefined, { raw: true })
      assert.ok([403, 404].includes(denied.status))
    }
    await openPermissions()
    assert.equal(await toggle.isChecked(), false)
    await saveSwitch(true)
    await toggle.locator('xpath=ancestor::label').scrollIntoViewIfNeeded()
    await admin.locator('.el-message:visible').first().waitFor({ state: 'hidden' })
    const screenshot = 'screens/01-个人有效权限-允许查看所有项目.png'
    await permissions.locator('.el-dialog').screenshot({ path: path.join(OUT, screenshot), animations: 'disabled' })
    return screenshot
  })
  await step('SCOPE-02', '同一个登录会话跨团队查看三类项目，权限不进入提醒候选', async () => {
    for (const project of projects) {
      await viewer.goto(WEB + project.page)
      await viewer.getByRole('heading', { name: project.title, exact: true }).waitFor()
    }
    const detail = await call('chenjing', 'GET', `/operations/tasks/${task.id}`)
    assert.equal(detail.can_edit, false)
    assert.ok(!detail.participants.some(person => person.id === account.id))
  })
  await step('SCOPE-03', '关闭开关后同一个登录会话立即失去跨团队访问', async () => {
    await openPermissions()
    assert.equal(await toggle.isChecked(), true)
    await saveSwitch(false)
    for (const project of projects) {
      const denied = await call('chenjing', 'GET', project.api, undefined, { raw: true })
      assert.ok([403, 404].includes(denied.status))
    }
  })
} finally {
  const current = await call('admin', 'GET', endpoint + '/effective-permissions')
  if (current.all_project_access !== before.all_project_access) {
    await call('admin', 'PUT', endpoint + '/permissions', { role_codes: before.assigned_roles,
      permissions: before.individual_permissions, expected_revision: current.permission_revision,
      all_project_access: before.all_project_access })
  }
  await browser.close()
  if (save().failed) process.exitCode = 1
}
