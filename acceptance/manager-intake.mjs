// 经理资料接入浏览器回归。仅允许本机独立测试 API，不触碰生产库。
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const API = process.env.API || 'http://127.0.0.1:18100'
const WEB = process.env.WEB || 'http://127.0.0.1:15180'
const OUT = process.env.OUT || '/tmp/jucheng-intake-evidence-20261030'
assert.equal(new URL(API).hostname, '127.0.0.1')
assert.equal(new URL(API).port, '18100')
const health = await fetch(`${API}/health`).then(r => r.json())
assert.equal(health.env, 'test')
assert.equal(health.mode, 'mock')
fs.mkdirSync(OUT, { recursive: true })
const marker = Date.now().toString()
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = []
let checks = 0
function check(name, passed = true) { assert.ok(passed, name); checks++; console.log('PASS', name) }
async function request(route, headers, body) {
  const r = await fetch(`${API}/api/v1${route}`, { headers: { ...headers, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }) }).then(r => r.json())
  assert.equal(r.code, 0, r.message)
  return r.data
}
async function login(username) {
  const result = await request('/auth/login', {}, { username, password: process.env.DEMO_PASSWORD || 'Jc@2026' })
  return { Authorization: `Bearer ${result.token}` }
}
async function newPage(headers, width = 1440) {
  const context = await browser.newContext({ viewport: { width, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
  await context.addInitScript(token => localStorage.setItem('jc_token', token), headers.Authorization.slice(7))
  const page = await context.newPage()
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(`${WEB}/imports`)
  await page.getByText('资料导入', { exact: true }).last().waitFor()
  return page
}
async function waitForBatch(headers, filename) {
  for (let i = 0; i < 50; i++) {
    const result = await request('/imports?page_size=100', headers)
    const batch = result.items.find(x => x.filename === filename && x.status !== 'processing')
    if (batch) return batch
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error('识别没有完成')
}

try {
  const manager = await login('wujun')
  const reviewer = await login('huangqi')
  const outsider = await login('xiekai')
  const page = await newPage(manager)
  const filenames = [`巡检规范A-${marker}.txt`, `巡检规范B-${marker}.txt`]
  const files = filenames.map((name, i) => ({ name, mimeType: 'text/plain',
    buffer: Buffer.from(`机房运维巡检规范\n隔离检索${marker}，第${i + 1}份资料。每周检查交换机、UPS 和温度，记录设备编号及异常，责任人核对后归档。`) }))
  await page.locator('input[type=file]').first().setInputFiles(files)
  const batches = await Promise.all(filenames.map(name => waitForBatch(manager, name)))
  await page.getByRole('button', { name: '刷新进度', exact: true }).click()
  for (const filename of filenames) await page.getByRole('row').filter({ hasText: filename }).waitFor()
  check('经理可批量上传，默认建议本部门可见', batches.every(b => b.default_visibility.visibility === 'dept' && b.default_visibility.visible_depts.includes('SC')))
  check('上传后未误报已入库', batches.every(b => b.stage === 'confirmation' && !b.ready))
  const firstRow = page.getByRole('row').filter({ hasText: filenames[0] })
  await firstRow.getByRole('button', { name: '详情', exact: true }).click()
  const detail = page.getByRole('dialog')
  await detail.getByText(/正文摘录/).waitFor()
  check('可看到摘要、分类依据和正文摘录', (await detail.innerText()).includes('识别依据'))
  await page.keyboard.press('Escape')
  await detail.waitFor({ state: 'hidden' })
  for (const filename of filenames) {
    const row = page.getByRole('row').filter({ hasText: filename })
    await row.locator('label.el-checkbox').click()
    assert.ok(await row.getByRole('checkbox').isChecked())
  }
  await page.getByRole('button', { name: /按建议提交.*2.*份/ }).click()
  const confirm = page.getByRole('dialog', { name: '核对批量提交' })
  await confirm.waitFor()
  check('批量确认明确展示分类与范围', (await confirm.innerText()).includes('SC') && (await confirm.innerText()).includes('客服'))
  await confirm.getByRole('button', { name: '确认提交', exact: true }).click()
  await firstRow.getByText('待审核', { exact: true }).waitFor()
  check('确认后显示待审核，而非已可检索')
  const submitted = await Promise.all(batches.map(b => request(`/imports/${b.id}`, manager)))
  for (const b of submitted) await request(`/kb/docs/${b.kb_doc.id}/review`, reviewer, { approve: true })
  await page.getByRole('button', { name: '刷新进度', exact: true }).click()
  await firstRow.getByText('已发布', { exact: true }).waitFor()
  check('审核发布后导入进度即时反映真实状态')
  const query = encodeURIComponent(`隔离检索${marker}`)
  const ownSearch = await request(`/kb/search?q=${query}&limit=50`, manager)
  const otherSearch = await request(`/kb/search?q=${query}&limit=50`, outsider)
  const ids = submitted.map(b => b.kb_doc.id)
  check('本人能检索已发布资料，其他部门不能检索', ids.every(id => ownSearch.items.some(h => h.doc_id === id)) && ids.every(id => !otherSearch.items.some(h => h.doc_id === id)))
  const downloadPromise = page.waitForEvent('download')
  await firstRow.locator('a.fn').click()
  const download = await downloadPromise
  await download.saveAs(path.join(OUT, 'original.txt'))
  check('界面原件下载携带登录权限', fs.readFileSync(path.join(OUT, 'original.txt'), 'utf8').includes(marker))
  check('复制签名不能匿名或跨部门下载', (await fetch(API + batches[0].file.url)).status === 401 &&
    (await fetch(API + batches[0].file.url, { headers: outsider })).status === 404)
  await page.screenshot({ path: path.join(OUT, 'desktop.png'), fullPage: true })

  const importer = await login('songjia')
  const cleanPage = await newPage(importer)
  const csvName = `合同台账-${marker}.csv`
  await cleanPage.locator('input[type=file]').first().setInputFiles({ name: csvName, mimeType: 'text/csv',
    buffer: Buffer.from(`客户,合同金额,签约日期,业务线\n浏览器验证${marker},12000,2026-10-20,XY\n`) })
  const ledger = await waitForBatch(importer, csvName)
  await cleanPage.getByRole('button', { name: '刷新进度', exact: true }).click()
  await cleanPage.getByRole('row').filter({ hasText: csvName }).getByRole('button', { name: '核对并导入', exact: true }).click()
  await cleanPage.getByRole('button', { name: '下一步：清洗预览', exact: true }).click()
  await cleanPage.getByRole('button', { name: /确认导入/ }).click()
  await cleanPage.getByText('导入完成', { exact: true }).waitFor()
  const ledgerDone = await request(`/imports/${ledger.id}`, importer)
  check('从经理入口可核对字段并真正写入台账', ledgerDone.ready && ledgerDone.clean_job.stats.imported === 1)
  await cleanPage.screenshot({ path: path.join(OUT, 'ledger.png'), fullPage: true })

  const mobile = await newPage(manager, 390)
  await mobile.getByRole('button', { name: '识别详情', exact: true }).first().waitFor()
  await mobile.getByRole('button', { name: '识别详情', exact: true }).first().click()
  await mobile.getByRole('dialog').getByText(/正文摘录/).waitFor()
  await mobile.waitForTimeout(400) // 等待 Element Plus 弹窗进入动画完成，再核对手机实际可见范围。
  const box = await mobile.getByRole('dialog').boundingBox()
  check('手机可查看识别详情，弹窗不超出视口', box.x >= 0 && box.width <= 390 && box.y >= 0 && box.y < 900)
  await mobile.screenshot({ path: path.join(OUT, 'mobile.png'), animations: 'disabled' })
  await mobile.keyboard.press('Escape')
  await mobile.getByRole('dialog').waitFor({ state: 'hidden' })
  const mobileName = `手机巡检-${marker}.txt`
  await mobile.locator('input[type=file]').first().setInputFiles({ name: mobileName, mimeType: 'text/plain',
    buffer: Buffer.from(`手机验收合成资料${marker}：机房巡检时记录 UPS、交换机、空调状态及异常处理结果。`) })
  const mobileBatch = await waitForBatch(manager, mobileName)
  await mobile.getByRole('button', { name: '刷新进度', exact: true }).click()
  await mobile.locator('.card').filter({ hasText: mobileName }).getByRole('button', { name: '确认入库', exact: true }).click()
  const mobileForm = mobile.getByRole('dialog')
  await mobileForm.getByText('仅本人', { exact: true }).click()
  await mobile.waitForTimeout(400)
  const formBox = await mobileForm.boundingBox()
  check('手机确认表单可完整显示并选择私有范围', formBox.x >= 0 && formBox.width <= 390)
  await mobile.screenshot({ path: path.join(OUT, 'mobile-confirm.png'), animations: 'disabled' })
  await mobileForm.getByRole('button', { name: '确认入库', exact: true }).click()
  await mobileForm.waitFor({ state: 'hidden' })
  const mobileSubmitted = await request(`/imports/${mobileBatch.id}`, manager)
  check('手机提交保留本人选择的范围，仍待审核', mobileSubmitted.kb_doc.visibility === 'private' && mobileSubmitted.stage === 'review')
  check('浏览器无运行错误', errors.length === 0)
  console.log(`${checks} checks passed; evidence: ${OUT}`)
} catch (error) {
  for (const [i, context] of browser.contexts().entries()) {
    const page = context.pages()[0]
    if (page) await page.screenshot({ path: path.join(OUT, `failure-${i}.png`), fullPage: true })
  }
  throw error
} finally {
  await browser.close()
}
