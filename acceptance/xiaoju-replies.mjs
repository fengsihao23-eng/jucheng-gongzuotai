// 对话排版与交互验收：所有 API 均由本地夹具接管，不登录或写入实际业务系统。
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const WEB = process.env.WEB || 'http://127.0.0.1:5180'
const OUT = process.env.OUT || '/tmp/jucheng-xiaoju-replies'
const sid = 'b'.repeat(32), taskId = 'c'.repeat(32), nextTaskId = 'd'.repeat(32)
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(WEB).hostname), '只对本地页面运行合成验收')
fs.mkdirSync(OUT, { recursive: true })

const reply = `2026 年 10 月新签合同共 **5 份**，合同额合计 **138,422 元**，已收 **81,922 元**，未收 **56,500 元**。

### 合同明细

| 合同编号 | 合同 / 项目 | 签订日期 | 合同额（元） | 未收（元） |
| --- | --- | --- | ---: | ---: |
| HT-2026-0053 | 阳江特殊学校饭堂监管信息化平台建设项目 | 2026-10-26 | 56,500 | 56,500 |
| HT-2026-0090 | 阳江市第二中学触控一体机销售合同 | 2026-10-21 | 22,622 | 0 |
| HT-2026-0089 | 阳江高级中学机房环动主机维修项目 | 2026-10-21 | 5,500 | 0 |
| HT-2026-0088 | 阳江高级中学安检门屏幕更换配件 | 2026-10-15 | 3,800 | 0 |
| HT-2026-0087 | 校园餐智慧监管平台建设合作三方协议 | 2026-10-08 | 50,000 | 0 |

### 汇总说明

- **数字校园**：3 份，合计 129,122 元。
- **运维服务**：1 份，5,500 元。
- **综合智慧安防**：1 份，3,800 元。

> 演示环境数据。金额以合同和回款记录为准。

合同明细已整理为文件，可在下方下载。`
const code = 'const total = 56500 + 22622 + 5500 + 3800 + 50000\nconsole.log(total)\n'
const task = {
  id: taskId, session_id: sid, state: 'succeeded', created_at: 1793750000, updated_at: 1793750019,
  message: reply, steps: Array.from({ length: 8 }, (_, i) => ({ label: ['读取合同台账', '核对回款记录', '汇总统计', '整理合同明细'][i % 4], state: 'succeeded', message: '已核对演示数据' })),
  business_results: [{ title: '合同与回款 · 汇总', ok: true, data_mode: 'mock', message: '已核对 5 份合同。', files: [{ filename: '合同明细.csv', url: '/api/v1/files/export-demo', size: 1024 }] }],
  workspace_files: [{ path: 'reports/合同汇总.xlsx', size: 8192 }],
}
let finishTask = false, sends = 0
const errors = [], downloads = []
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1280 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', permissions: ['clipboard-read', 'clipboard-write'] })
await context.addInitScript(() => localStorage.setItem('jc_token', 'local-ui-fixture'))
await context.route('**/api/v1/**', async (route) => {
  const url = new URL(route.request().url()), endpoint = url.pathname.replace('/api/v1', '')
  const data = (value) => route.fulfill({ json: { code: 0, data: value, message: 'ok' } })
  if (endpoint === '/auth/me') return data({ id: 1, name: '界面验收', menus: [], buttons: [] })
  if (endpoint === '/assistant/catalog') return data({ connectors: [], is_admin: false, runtime: { available: true, model: 'deepseek-v4.1-flash-1017', models: ['deepseek-v4.1-flash-1017'], thinking: 'medium' } })
  if (endpoint === '/assistant/sessions') return data([{ id: sid, title: '上个月新签合同汇总', updated_at: Date.now() / 1000 }])
  if (endpoint === `/assistant/sessions/${sid}`) return data({ id: sid, model: 'deepseek-v4.1-flash-1017', thinking: 'medium', last_task_id: taskId, messages: [
    { role: 'user', text: '上个月新签合同汇总' },
    { role: 'assistant', text: reply, task_id: taskId },
  ] })
  if (endpoint === `/assistant/tasks/${taskId}`) return data(task)
  if (endpoint === '/assistant/tasks' && route.request().method() === 'POST') {
    sends++
    return data({ ...task, id: nextTaskId, state: 'running', steps: [], partial_text: '', business_results: [], workspace_files: [] })
  }
  const nextTask = () => ({ ...task, id: nextTaskId, state: finishTask ? 'succeeded' : 'running',
    partial_text: `### 核对结果\n\n| 合同数 | 合同额 |\n| ---: | ---: |\n| 5 | 138,422 |\n\n\`\`\`javascript\n${code}${finishTask ? '\`\`\`' : ''}`,
    message: `核对完成，合计 **138,422 元**。\n\n\`\`\`javascript\n${code}\`\`\``,
    business_results: [], workspace_files: [], steps: [{ label: '正在核对合同金额', state: finishTask ? 'succeeded' : 'running' }],
  })
  if (endpoint === `/assistant/tasks/${nextTaskId}/stream`) return route.fulfill({ contentType: 'text/event-stream', body: `event: task\ndata: ${JSON.stringify(nextTask())}\n\n` })
  if (endpoint === `/assistant/tasks/${nextTaskId}`) return data(nextTask())
  if (endpoint === `/assistant/sessions/${sid}/files` || endpoint === '/files/export-demo') {
    downloads.push({ endpoint, authorization: route.request().headers().authorization })
    return route.fulfill({ contentType: 'application/octet-stream', headers: { 'content-disposition': 'attachment; filename="fixture.csv"' }, body: 'local-download-fixture' })
  }
  if (endpoint === '/assistant/tasks') return data([])
  return route.fulfill({ status: 500, json: { code: 50000, message: `未预期的合成请求 ${endpoint}` } })
})
const page = await context.newPage()
page.on('pageerror', (error) => errors.push(error.message))
let checks = 0
const check = (label, value) => { assert.ok(value, label); checks++; console.log('PASS', label) }
try {
  await page.goto(`${WEB}/xiaoju/${sid}`)
  await page.locator('.md table tbody tr').nth(4).waitFor()
  check('历史回复恢复标题、加粗和五行合同明细', await page.locator('.md h3').count() === 2 && (await page.locator('.md strong').allTextContents()).includes('138,422 元'))
  check('工作过程默认收起', await page.locator('.proc-hd').getAttribute('aria-expanded') === 'false')
  await page.locator('.proc-hd').click()
  check('展开可查看真实步骤时间线', await page.locator('.proc-bd .steps li').count() === 8 && await page.locator('.proc-bd').isVisible())
  await page.locator('.proc-hd').click()
  check('回执折叠时两种来源的下载卡片仍可见', await page.locator('.file-card').count() === 2 && await page.locator('.receipts').getAttribute('open') === null)
  await page.locator('.scroll').evaluate((element) => { element.style.scrollBehavior = 'auto'; element.scrollTop = 0 })
  await page.screenshot({ path: path.join(OUT, 'desktop.png'), fullPage: true })
  await page.getByRole('button', { name: '复制表格', exact: true }).click()
  check('表格复制为可直接粘贴进表格软件的单元格', (await page.evaluate(() => navigator.clipboard.readText())).includes('HT-2026-0053\t阳江特殊学校饭堂监管信息化平台建设项目\t2026-10-26\t56,500\t56,500'))
  await page.getByRole('button', { name: '复制回复', exact: true }).click()
  check('整条回复复制保留原始 Markdown', (await page.evaluate(() => navigator.clipboard.readText())) === reply)
  for (const filename of ['合同明细.csv', '合同汇总.xlsx']) {
    const pending = page.waitForEvent('download')
    await page.getByRole('button', { name: `下载 ${filename}`, exact: true }).click()
    const download = await pending
    check(`${filename}可通过本人登录态下载`, downloads.at(-1)?.authorization === 'Bearer local-ui-fixture' && !!download.suggestedFilename())
  }
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    await page.waitForFunction(() => document.querySelector('.side').getBoundingClientRect().right <= 1)
    await page.locator('.scroll').evaluate((element) => { element.scrollTop = 0 })
    check(`${width}px手机页面无横向溢出`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    check(`${width}px宽表格在自身区域滚动`, await page.locator('.md-table-scroll').evaluate((element) => element.scrollWidth > element.clientWidth))
    const box = await page.getByRole('button', { name: '发送需求', exact: true }).boundingBox()
    check(`${width}px发送按钮保持在屏幕内`, box && box.x >= 0 && box.x + box.width <= width)
    if (width === 390) await page.screenshot({ path: path.join(OUT, 'mobile.png'), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.getByRole('textbox', { name: '向小聚描述需求' }).fill('请核对合同金额并给计算过程')
  await page.getByRole('textbox', { name: '向小聚描述需求' }).dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
  check('中文输入法选词时不会误发消息', sends === 0)
  await page.getByRole('textbox', { name: '向小聚描述需求' }).press('Shift+Enter')
  check('Shift+Enter只换行', sends === 0)
  await page.getByRole('button', { name: '发送需求', exact: true }).click()
  await page.locator('.md.streaming .md-code').waitFor()
  check('流式回复可显示尚未闭合的代码块与表格', await page.locator('.md.streaming table').count() === 1)
  const liveProcess = page.locator(`[data-task-id="${nextTaskId}"]`)
  check('执行中默认呈现紧凑进度', await liveProcess.locator('.proc-hd').getAttribute('aria-expanded') === 'false')
  await liveProcess.locator('.proc-hd').click()
  check('执行中可以主动展开工作过程', await liveProcess.locator('.proc-bd').isVisible())
  finishTask = true
  await page.locator(`[data-task-id="${nextTaskId}"][data-task-state="succeeded"]`).waitFor()
  await page.locator('.md.streaming').waitFor({ state: 'detached' })
  check('完成后切换为最终回复并收起过程', await liveProcess.locator('.proc-hd').getAttribute('aria-expanded') === 'false')
  await page.getByRole('button', { name: '复制代码', exact: true }).click()
  check('代码复制保留缩进与换行', await page.evaluate(() => navigator.clipboard.readText()) === code)
  check('页面没有运行时错误', errors.length === 0)
  console.log(`${checks} checks passed; screenshots: ${OUT}`)
} finally { await browser.close() }
