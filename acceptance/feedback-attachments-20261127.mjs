import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, OUT, WEB, call, loginPage, save, step } from './lib.mjs'

for (const url of [WEB, API]) assert.ok(['127.0.0.1', 'localhost'].includes(new URL(url).hostname))
const health = await fetch(`${API}/health`).then(response => response.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')
assert.equal(health.overrides, '')

const files = [
  { name: '现场签到表', mimeType: 'application/octet-stream', buffer: Buffer.from('反馈附件验收：签到表\n') },
  { name: '设备交接清单.rawdata', mimeType: 'application/x-fixture', buffer: Buffer.from([0, 15, 170, 131, 12, 255]) },
]
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const manager = await loginPage(browser, 'admin', { width: 1440, height: 1024 })
const assignee = await loginPage(browser, 'xiekai', { width: 1440, height: 1024 })
const admin = await call('admin', 'GET', '/auth/me')
const operator = await call('xiekai', 'GET', '/auth/me')
const button = (locator, name) => locator.getByRole('button', { name, exact: true })
const dialog = (page, name) => page.getByRole('dialog', { name, exact: true })
const title = `确认试点学校设备交接 ${Date.now().toString().slice(-5)}`

async function selectFiles(page, root, selected) {
  const chooser = page.waitForEvent('filechooser')
  await button(root, '上传附件').click()
  await (await chooser).setFiles(selected)
}

async function submit(page, root, name, route) {
  const posted = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(route))
  await button(root, name).click()
  const result = await posted
  assert.equal(result.status(), 200, `${route} ${result.status()} ${await result.text()}`)
}

async function download(page, root, file) {
  const pending = page.waitForEvent('download')
  await button(root, `下载 ${file.name}`).click()
  const result = await pending
  assert.equal(result.suggestedFilename(), file.name)
  assert.deepEqual(await fs.readFile(await result.path()), file.buffer)
}

async function capture(page, name) {
  await page.evaluate(() => document.fonts.ready)
  await page.locator('.el-message').first().waitFor({ state: 'hidden' })
  const filename = `screens/${name}.png`
  await page.screenshot({ path: path.join(OUT, filename), animations: 'disabled' })
  return filename
}

try {
  const ops = await call('admin', 'POST', '/operations', { name: '智慧教室交接验收', owner_id: admin.id })
  const opsPath = `/operations/${ops.id}`
  await call('admin', 'POST', `${opsPath}/members`, { user_id: operator.id, role: 'member' })
  const task = await call('admin', 'POST', `${opsPath}/tasks`, {
    title, owner_id: operator.id, due_date: '2026-11-28', cadence: 'daily',
    description: '现场核对设备编号、交接清单和签收记录。',
  })

  await step('ATTACH-01', '从工作台打开任务，上传多个任意格式文件，从过程记录下载原件', async () => {
    await assignee.goto(WEB)
    await assignee.getByRole('button', { name: new RegExp(title) }).click()
    const feedback = dialog(assignee, '任务详情')
    await feedback.getByRole('textbox', { name: '目前进度情况', exact: true }).fill('已完成设备数量和编号清点，下一步由校方确认交接记录。')
    const removed = { name: '已移除文件.bin', mimeType: 'application/octet-stream', buffer: Buffer.from('removed') }
    await selectFiles(assignee, feedback, [...files, removed])
    await button(feedback, `移除 ${removed.name}`).click()
    const picker = await capture(assignee, '01-工作台任务反馈上传附件')
    await submit(assignee, feedback, '提交进展', `/operations/tasks/${task.id}/feedback`)
    await button(feedback, '过程记录').click()
    const history = dialog(assignee, '过程记录')
    await button(history, `下载 ${files[0].name}`).waitFor()
    assert.equal(await button(history, `下载 ${removed.name}`).count(), 0)
    const timeline = await capture(assignee, '02-过程记录下载附件')
    for (const file of files) await download(assignee, history, file)
    return { picker, timeline }
  })

  await step('ATTACH-02', '运营项目重新打开同一任务后还能下载已保存的附件', async () => {
    await manager.goto(`${WEB}${opsPath}?task=${task.id}`)
    const feedback = dialog(manager, '任务详情')
    await button(feedback, '过程记录').click()
    await download(manager, dialog(manager, '过程记录'), files[1])
  })

  for (const scope of ['key', 'general']) {
    await step(`ATTACH-${scope}`, `${scope === 'key' ? '重点项目' : '一般项目'}任务的附件随反馈留在过程记录`, async () => {
      const plan = { owner_id: admin.id, preparation_on: '2026-11-27', due_on: '2026-12-05' }
      const project = await call('admin', 'POST', `/market/projects?scope=${scope}`, {
        ...plan, name: `${scope === 'key' ? '重点' : '一般'}项目附件验收`, tasks: [{ ...plan, title: '确认项目验收材料' }],
      })
      const info = await call('admin', 'GET', `/market/projects/${project.project_code}`, { scope })
      const taskId = info.tasks[0].id
      const route = scope === 'key' ? `/market/projects/${project.project_code}` : `/general-projects/${project.project_code}`
      await manager.goto(`${WEB}${route}?task=${taskId}`)
      const feedback = dialog(manager, '任务情况')
      await feedback.getByRole('textbox', { name: scope === 'key' ? '重点任务进展' : '任务进展', exact: true }).fill('已整理验收材料，附件保留原文件。')
      await selectFiles(manager, feedback, [files[1]])
      await submit(manager, feedback, '提交进展', `/tasks/${taskId}/feedback`)
      await button(feedback, '过程记录').click()
      await download(manager, dialog(manager, '过程记录'), files[1])
    })
  }

  await step('ATTACH-05', '学校跟进反馈附档后在学校记录内查阅', async () => {
    const schoolName = '设备试点学校'
    const school = await call('admin', 'POST', `${opsPath}/schools`, { name: schoolName })
    await manager.goto(WEB + opsPath)
    await manager.getByRole('tab', { name: '市场开拓', exact: true }).click()
    await button(manager, `查看学校跟进：${schoolName}`).click()
    const schoolDialog = dialog(manager, schoolName)
    await button(schoolDialog, '填写跟进反馈').click()
    const feedback = dialog(manager, '跟进反馈')
    await feedback.getByRole('textbox', { name: '目前进度情况', exact: true }).fill('校方已核对现场签到记录。')
    await selectFiles(manager, feedback, [files[0]])
    await submit(manager, feedback, '提交进展', `/schools/${school.id}/visits`)
    await download(manager, schoolDialog, files[0])
  })

  await step('ATTACH-06', '共用反馈弹窗可为零星回款任务上传并下载附件', async () => {
    const project = await call('admin', 'POST', '/collections/sporadic', {
      name: '零星回款附件验收', customer: '试点客户', owner_id: admin.id, expected_amount: 100, due_on: '2026-12-05',
    })
    await manager.goto(`${WEB}/collections/sporadic?project=${project.id}`)
    const feedback = dialog(manager, '零星回款任务详情')
    await feedback.getByRole('textbox', { name: '目前进度情况', exact: true }).fill('已和客户确认回款安排。')
    await selectFiles(manager, feedback, [files[0]])
    await submit(manager, feedback, '提交进展', `/record-followups/sporadic-collection/${project.id}`)
    await button(feedback, '过程记录').click()
    await download(manager, dialog(manager, '过程记录'), files[0])
  })
} finally {
  await browser.close()
  const { failed } = save()
  if (failed) process.exitCode = 1
}
