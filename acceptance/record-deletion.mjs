import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { chromium } from 'playwright'
import { API, DELETE_PASSWORD, WEB, call, loginPage, save, shot, step } from './lib.mjs'

const health = await fetch(API + '/health').then(r => r.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')
for (const url of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname))

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const manager = await loginPage(browser, 'admin', { width: 1440, height: 1050 })
const finance = await loginPage(browser, 'chenjing')
const profile = await call('admin', 'GET', '/auth/me')
const suffix = randomUUID().slice(0, 6)

async function openRemoval(page, locator, title) {
  await locator.click({ button: 'right' })
  await page.getByRole('menu', { name: '记录操作' }).getByRole('menuitem', { name: '删除', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: title, exact: true })
  await dialog.waitFor()
  return dialog
}

async function confirm(dialog, password) {
  await dialog.getByLabel('删除密码', { exact: true }).fill(password)
  await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
}

async function capture(page, name) {
  await page.waitForFunction(() => document.getAnimations().every(animation =>
    animation.playState !== 'running' || animation.effect?.getTiming().iterations === Infinity))
  return shot(page, name)
}

try {
  await step('DEL-01', '从零星回款右键删除，取消和错误密码保留记录', async () => {
    const name = '验收零星回款-' + suffix
    await call('admin', 'POST', '/collections/sporadic', { name, owner_id: profile.id })
    await manager.goto(WEB + '/collections/sporadic')
    const row = manager.getByText(name, { exact: true })
    let dialog = await openRemoval(manager, row, '删除零星回款项目')
    await capture(manager, '01-right-click-password')
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await dialog.waitFor({ state: 'hidden' })
    await row.waitFor()
    dialog = await openRemoval(manager, row, '删除零星回款项目')
    await confirm(dialog, '不正确的测试口令')
    await dialog.getByText('删除密码不正确，请重新输入').waitFor()
    await confirm(dialog, DELETE_PASSWORD)
    await dialog.waitFor({ state: 'hidden' })
    await row.waitFor({ state: 'hidden' })
    await manager.reload()
    await manager.getByRole('button', { name: '登记零星回款' }).waitFor()
    assert.equal(await row.count(), 0)
  })

  await step('DEL-02', '系统管理可查看和改密，业务账号无法进入', async () => {
    await manager.goto(WEB + '/admin?tab=deletion')
    const current = manager.getByRole('textbox', { name: '当前删除密码' })
    const next = manager.getByLabel('新删除密码', { exact: true })
    await manager.getByRole('button', { name: '查看密码', exact: true }).click()
    await manager.getByRole('button', { name: '隐藏密码', exact: true }).waitFor()
    assert.equal(await current.inputValue(), DELETE_PASSWORD)
    await manager.getByRole('button', { name: '隐藏密码', exact: true }).click()
    await capture(manager, '02-delete-password-settings')
    await finance.goto(WEB + '/admin?tab=deletion')
    await finance.waitForURL('**/403')
    const denied = await call('chenjing', 'GET', '/admin/deletion-password', { reveal: true }, { raw: true })
    assert.equal(denied.status, 403)

    const rotated = '隔离测试-' + suffix
    try {
      await next.fill(rotated)
      await manager.getByRole('button', { name: '保存新密码', exact: true }).click()
      await manager.getByText('删除密码已更新', { exact: true }).waitFor()
      const name = '验收客服工单-' + suffix
      await call('admin', 'POST', '/service/tickets', { customer_name: name, problem_desc: '验收用：设备显示异常' })
      await manager.goto(WEB + '/service')
      const row = manager.getByText(name, { exact: true }).first()
      const dialog = await openRemoval(manager, row, '删除工单')
      await confirm(dialog, DELETE_PASSWORD)
      await dialog.getByText('删除密码不正确，请重新输入').waitFor()
      await confirm(dialog, rotated)
      await dialog.waitFor({ state: 'hidden' })
      await row.waitFor({ state: 'hidden' })
    } finally {
      const info = await call('admin', 'GET', '/admin/deletion-password')
      await call('admin', 'PUT', '/admin/deletion-password', { password: DELETE_PASSWORD, expected_revision: info.revision })
    }
  })

  await step('DEL-03', '对手信息可从项目页右键删除，刷新后仍已移除', async () => {
    const project = await call('admin', 'POST', '/operations', {
      name: '删除验收运营项目-' + suffix, category: '智慧校园', owner_id: profile.id, enabled_sections: ['progress'],
    })
    const path = '/operations/' + project.id
    const name = '验收对手资料-' + suffix
    await call('admin', 'POST', path + '/competitors', { name, details: '合成数据' })
    await manager.goto(WEB + path)
    await manager.getByRole('tab', { name: '对手信息' }).click()
    const row = manager.getByText(name, { exact: true })
    const dialog = await openRemoval(manager, row, '删除对手信息')
    await capture(manager, '03-operations-delete')
    await confirm(dialog, DELETE_PASSWORD)
    await dialog.waitFor({ state: 'hidden' })
    await manager.getByText('暂无对手信息', { exact: true }).waitFor()
    assert.equal((await call('admin', 'GET', path + '/competitors')).items.length, 0)
  })
} finally {
  await browser.close()
}

const result = save()
if (result.failed) process.exitCode = 1
