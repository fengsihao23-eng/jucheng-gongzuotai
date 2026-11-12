import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, PWD, OUT, call, loginPage, save, step } from './lib.mjs'

const health = await fetch(API + '/health').then(response => response.json())
assert.equal(health.env, 'test', '保存权限测试只在隔离环境运行')
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const member = (await call('admin', 'GET', '/admin/users', { keyword: 'chenjing' })).items[0]
const path = '/admin/users/' + member.id
const before = await call('admin', 'GET', path + '/effective-permissions')
const page = await loginPage(browser, 'admin')
const card = () => page.getByRole('dialog', { name: '个人有效权限', exact: true })
let putCount = 0
page.on('request', request => {
  if (request.method() === 'PUT' && new URL(request.url()).pathname.endsWith(path + '/permissions')) putCount++
})
async function openMember() {
  await page.goto(WEB + '/admin')
  await page.getByRole('button', { name: /^财务部，/ }).click()
  await page.getByRole('dialog', { name: '财务部 · 部门人员' }).getByRole('button', { name: '查看 ' + member.name + ' 的权限' }).click()
  await card().getByRole('heading', { name: member.name, exact: true }).waitFor()
}

try {
  await step('SAVE01', '未修改时保存可点击并给出核对结果', async () => {
    await openMember()
    const button = card().getByRole('button', { name: '保存此人权限', exact: true })
    assert.equal(await button.isDisabled(), false)
    await button.click()
    await card().getByRole('status').getByText('已核对，当前权限已保存', { exact: true }).waitFor()
    assert.equal(putCount, 0)
  })

  await step('SAVE02', '过期身份提示后核验并继续保存原选择', async () => {
    await card().getByText('单独配置', { exact: true }).click()
    await card().locator('.personal-settings .el-checkbox').filter({ hasText: '流水核对' }).click()
    let first = true
    await page.route('**/api/v1' + path + '/permissions', async route => {
      if (route.request().method() === 'PUT' && first) {
        first = false
        return route.fulfill({ status: 403, json: { code: 40301, message: '请先核验本人身份，再保存权限',
          data: { requires_reauthentication: true, password_available: true } } })
      }
      return route.continue()
    })
    await card().getByRole('button', { name: '保存此人权限', exact: true }).click()
    const verification = page.getByRole('dialog', { name: '确认本人身份', exact: true })
    await verification.waitFor()
    await verification.getByLabel('核验登录密码').fill('incorrect-permission-test')
    await verification.getByRole('button', { name: '核验并保存' }).click()
    await verification.getByText('密码不正确，请重新输入', { exact: true }).waitFor()
    await verification.getByLabel('核验登录密码').fill(PWD)
    const saved = page.waitForResponse(response => response.url().endsWith(path + '/permissions') &&
      response.request().method() === 'PUT' && response.status() === 200)
    await verification.getByRole('button', { name: '核验并保存' }).click()
    await saved
    await card().getByRole('status').getByText('已保存 ' + member.name + ' 的权限', { exact: true }).waitFor()
    assert.equal(await card().getByRole('button', { name: '保存此人权限' }).isDisabled(), false)
    const actual = await call('admin', 'GET', path + '/effective-permissions')
    assert.ok(!actual.individual_permissions.menus.includes('flows'))
    await page.unroute('**/api/v1' + path + '/permissions')
  })

  await step('SAVE03', '保存错误始终出现在按钮旁且保留选择', async () => {
    await card().locator('.personal-settings .el-checkbox').filter({ hasText: '商机信息' }).click()
    await page.route('**/api/v1' + path + '/permissions', route => route.fulfill({
      status: 400, json: { code: 40001, message: '测试保存错误，请重新核对', data: null },
    }))
    await card().getByRole('button', { name: '保存此人权限' }).click()
    const error = card().locator('.permission-footer [role="alert"]')
    await error.getByText('测试保存错误，请重新核对', { exact: true }).waitFor()
    const box = await error.boundingBox()
    assert.ok(box && box.y >= 0 && box.y + box.height <= 901)
    assert.equal(await card().locator('.personal-settings .el-checkbox').filter({ hasText: '商机信息' }).getByRole('checkbox').isChecked(), true)
    assert.equal(await card().getByRole('button', { name: '保存此人权限' }).isDisabled(), false)
    await page.unroute('**/api/v1' + path + '/permissions')
    await card().screenshot({ path: OUT + '/screens/save-error-visible.png', animations: 'disabled' })
  })

  await step('SAVE04', '重新登录后恢复未保存权限并可继续保存', async () => {
    let first = true
    await page.route('**/api/v1' + path + '/permissions', async route => {
      if (route.request().method() === 'PUT' && first) {
        first = false
        return route.fulfill({ status: 403, json: { code: 40301, message: '请先核验本人身份，再保存权限',
          data: { requires_reauthentication: true, password_available: false } } })
      }
      return route.continue()
    })
    await card().getByRole('button', { name: '保存此人权限' }).click()
    const verify = page.getByRole('dialog', { name: '确认本人身份', exact: true })
    await verify.getByRole('button', { name: '重新登录' }).click()
    await page.waitForURL(/\/login\?redirect=/)
    await page.getByPlaceholder('企业微信账号 / 姓名 / 手机号').fill('admin')
    await page.getByPlaceholder('密码', { exact: true }).fill(PWD)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.waitForURL(/\/admin\?tab=users/)
    await card().getByRole('status').getByText('已恢复刚才的选择，请点击保存', { exact: true }).waitFor()
    assert.equal(await card().locator('.personal-settings .el-checkbox').filter({ hasText: '商机信息' }).getByRole('checkbox').isChecked(), true)
    const saved = page.waitForResponse(response => response.url().endsWith(path + '/permissions') &&
      response.request().method() === 'PUT' && response.status() === 200)
    await card().getByRole('button', { name: '保存此人权限' }).click()
    await saved
    await card().getByRole('status').getByText('已保存 ' + member.name + ' 的权限', { exact: true }).waitFor()
    await page.unroute('**/api/v1' + path + '/permissions')
    assert.ok((await call('admin', 'GET', path + '/effective-permissions')).individual_permissions.menus.includes('opportunities'))
  })
} finally {
  const state = await call('admin', 'GET', path + '/effective-permissions')
  await call('admin', 'PUT', path + '/permissions', { role_codes: before.assigned_roles,
    permissions: before.individual_permissions, expected_revision: state.permission_revision })
  await browser.close()
}
const result = save()
if (result.failed) process.exitCode = 1
