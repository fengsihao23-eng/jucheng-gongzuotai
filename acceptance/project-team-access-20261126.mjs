import assert from 'node:assert/strict'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, OUT, WEB, call, loginPage, save, step } from './lib.mjs'

for (const url of [API, WEB]) assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname))
const health = await fetch(API + '/health').then(r => r.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')
assert.equal(health.overrides, '')

const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const manager = await loginPage(browser, 'wujun', { width: 1440, height: 1050 })
const outsider = await loginPage(browser, 'admin')
const accounts = Object.fromEntries(await Promise.all(['wujun', 'xuhao', 'admin'].map(async name => [
  name, await call(name, 'GET', '/auth/me'),
])))
const managerId = accounts.wujun.id
const teammate = accounts.xuhao
const button = (root, name) => root.getByRole('button', { name, exact: true })
const dialog = (page, name) => page.getByRole('dialog', { name, exact: true })
const wait = locator => locator.waitFor({ state: 'visible', timeout: 12000 })
const taskTitle = '核对试点学校交接清单'
const marker = Date.now().toString().slice(-6)
const screenshots = {
  operations: '02-运营项目提醒候选',
  key: '03-重点项目提醒候选',
  general: '04-一般项目提醒候选',
}

async function capture(page, name) {
  await page.evaluate(() => document.fonts.ready)
  const file = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, file), animations: 'disabled' })
  return file
}

try {
  const operations = await call('wujun', 'POST', '/operations', { name: `团队权限验收 ${marker}`, owner_id: managerId })
  const operationPath = `/operations/${operations.id}`
  await call('wujun', 'POST', operationPath + '/members', { user_id: teammate.id, role: 'member' })
  const school = await call('wujun', 'POST', operationPath + '/schools', { name: '智慧教室试点学校' })
  const opsTask = await call('wujun', 'POST', operationPath + '/tasks', {
    title: taskTitle, owner_id: managerId, due_date: '2026-12-09', description: '确认交接资料的份数和签收情况。',
  })

  await step('TEAM-01', '学校资料和跟进反馈使用居中弹窗，提交后刷新过程记录', async () => {
    await manager.goto(WEB + operationPath)
    await manager.getByRole('tab', { name: '市场开拓', exact: true }).click()
    await button(manager, '查看学校跟进：智慧教室试点学校').click()
    const schoolDialog = dialog(manager, school.name)
    assert.equal(await schoolDialog.locator('.el-drawer').count(), 0)
    await button(schoolDialog, '填写跟进反馈').click()
    const feedback = dialog(manager, '跟进反馈')
    await feedback.getByRole('radiogroup', { name: '完成情况', exact: true }).getByText('已完成', { exact: true }).click()
    await feedback.getByRole('textbox', { name: '完成结果', exact: true }).fill('已与校方核对交接资料，校方确认首批设备清单。')
    await feedback.getByRole('textbox', { name: '下一步', exact: true }).fill('安排试点安装并保留签收记录。')
    assert.ok(await feedback.getByRole('radio', { name: '已完成', exact: true }).isChecked())
    const evidence = await capture(manager, '01-学校跟进反馈弹窗')
    await button(feedback, '提交进展').click()
    await feedback.waitFor({ state: 'hidden' })
    await wait(schoolDialog.getByText('已与校方核对交接资料，校方确认首批设备清单。', { exact: true }))
    assert.equal((await call('wujun', 'GET', `${operationPath}/schools/${school.id}/visits`)).items[0].status, 'done')
    return evidence
  })

  for (const kind of ['operations', 'key', 'general']) {
    await step(`TEAM-${kind}`, '验证任务提醒候选、实际通知与团队访问', async () => {
      const isOps = kind === 'operations'
      const general = kind === 'general'
      let projectPath = operationPath, taskId = opsTask.id
      if (!isOps) {
        const created = await call('wujun', 'POST', `/market/projects?scope=${kind}`, {
          name: `${general ? '一般项目' : '重点项目'}团队验收 ${marker}`,
          owner_id: managerId, preparation_on: '2026-11-20', due_on: '2026-12-10', team_member_ids: [teammate.id],
          tasks: [{ title: taskTitle, owner_id: managerId, preparation_on: '2026-11-20', due_on: '2026-12-09',
            requirements: '确认交接资料的份数和签收情况。' }],
        })
        projectPath = general ? `/general-projects/${created.project_code}` : `/market/projects/${created.project_code}`
        const detail = await call('wujun', 'GET', `/market/projects/${created.project_code}?scope=${kind}`)
        taskId = detail.tasks[0].id
      }
      const taskUrl = `${projectPath}?task=${taskId}`
      await manager.goto(WEB + taskUrl)
      const taskDialog = dialog(manager, isOps ? '任务详情' : '任务情况')
      await wait(taskDialog.getByRole('heading', { name: taskTitle, exact: true }))
      const field = taskDialog.getByRole('combobox', { name: '讨论接收人', exact: true })
      await field.locator('xpath=ancestor::div[contains(@class,"el-select__wrapper")]').click()
      const option = manager.getByRole('option', { name: teammate.name, exact: true })
      await wait(option)
      assert.deepEqual(await manager.getByRole('option').allTextContents(), [teammate.name])
      const evidence = await capture(manager, screenshots[kind])
      await option.click()
      await taskDialog.getByRole('textbox', { name: '要求或留言', exact: true }).fill('请核对清单并反馈签收结果。')
      await button(taskDialog, '发送讨论').click()
      await wait(manager.getByText('讨论已保存', { exact: true }))
      const inbox = await call('xuhao', 'GET', '/notifications', { page_size: 100 })
      const title = isOps ? '运营任务有新留言' : '有成员在记录中提醒你'
      const notice = inbox.items.find(n => n.title === title && (isOps ? n.link === taskUrl : n.link.startsWith('/record-followups/')))
      assert.ok(notice)
      const outbox = await call('admin', 'GET', '/dev/mock/outbox', { limit: 100 })
      assert.ok(outbox.some(row => row.title === title && row.to.join(',') === 'xuhao' && row.url?.endsWith(notice.link)))
      await outsider.goto(WEB + taskUrl)
      await wait(outsider.getByText(isOps ? /无法打开|无法读取|无权/ : /无法打开|无权/).first())
      assert.equal(await outsider.getByRole('heading', { name: taskTitle, exact: true }).count(), 0)
      return evidence
    })
  }
} finally {
  await browser.close()
  if (save().failed) process.exitCode = 1
}
