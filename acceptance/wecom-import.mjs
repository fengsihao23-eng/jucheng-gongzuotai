import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const API = 'http://127.0.0.1:18110', WEB = 'http://127.0.0.1:15190'
const OUT = '/tmp/jucheng-wecom-import-evidence-20261030'
assert.equal((await fetch(API + '/health').then(r => r.json())).env, 'test')
assert.equal((await fetch(API + '/intake-test-fixture').then(r => r.json())).fixture, 'wecom_import_synthetic')
fs.mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const errors = [], marker = Date.now().toString()
let checks = 0
function check(name, ok = true) { assert.ok(ok, name); console.log('PASS', name); checks++ }
async function login(name) {
  const r = await fetch(API + '/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: name, password: 'Jc@2026' }) }).then(r => r.json())
  assert.equal(r.code, 0)
  return r.data.token
}
const token = await login('songjia')
async function request(route, method = 'GET', body) {
  const r = await fetch(API + '/api/v1' + route, { method, headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }).then(r => r.json())
  assert.equal(r.code, 0, r.message); return r.data
}
async function pageFor(width = 1440) {
  const ctx = await browser.newContext({ viewport: { width, height: 1000 }, locale: 'zh-CN' })
  await ctx.addInitScript(t => localStorage.setItem('jc_token', t), token)
  const page = await ctx.newPage(); page.on('pageerror', e => errors.push(e.message))
  await page.goto(WEB + '/imports'); await page.getByRole('button', { name: '从企业微信导入', exact: true }).click()
  return page
}
try {
  const page = await pageFor(), dialog = page.getByRole('dialog')
  await dialog.getByLabel('企微资料链接').fill('https://doc.weixin.qq.com/smartpage/a1_test' + marker)
  await dialog.getByRole('button', { name: '读取预览', exact: true }).click()
  let card = dialog.locator('.preview').filter({ has: page.locator(`a[href="https://doc.weixin.qq.com/smartpage/a1_test${marker}"]`) }).first()
  await card.getByText('读取完成，待确认', { exact: true }).waitFor()
  check('智能文档逐页读取，展示正文与明确范围', (await card.innerText()).includes('child') && (await card.innerText()).includes('2 页正文'))
  await card.getByRole('button', { name: '确认保存到资料导入' }).click()
  await card.getByText('已保存快照', { exact: true }).waitFor()
  check('确认后接回资料导入，不将读取成功当发布成功')
  await dialog.getByLabel('企微资料链接').fill('https://doc.weixin.qq.com/smartsheet/s3_test' + marker)
  await dialog.getByRole('button', { name: '读取预览', exact: true }).click()
  card = dialog.locator('.preview').filter({ has: page.locator(`a[href="https://doc.weixin.qq.com/smartsheet/s3_test${marker}"]`) }).first()
  await card.getByText('请选择数据子表', { exact: true }).waitFor()
  await card.locator('.el-select').click()
  await page.getByRole('option', { name: '客户合同（2 条）', exact: true }).click()
  await card.getByRole('button', { name: '读取这个子表', exact: true }).click()
  card = dialog.locator('.preview').filter({ has: page.locator(`a[href="https://doc.weixin.qq.com/smartsheet/s3_test${marker}"]`) }).first()
  await card.getByText('读取完成，待确认', { exact: true }).waitFor()
  check('智能表格先选真实子表，完整显示数据样例', (await card.innerText()).includes('1001') && (await card.innerText()).includes('2 条数据'))
  await card.getByRole('button', { name: '确认保存到资料导入' }).click()
  await card.getByText('已保存快照', { exact: true }).waitFor()
  let batch
  for (let i = 0; i < 40; i++) {
    const rows = await request('/imports?page_size=100')
    batch = rows.items.find(x => x.suggestion.external_source?.url === `https://doc.weixin.qq.com/smartsheet/s3_test${marker}`)
    if (batch && batch.status !== 'processing') break
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  assert.ok(batch.clean_job)
  const imported = await request(`/clean/jobs/${batch.clean_job.id}/import`, 'POST')
  check('企微表格可经既有字段校验实际写入台账', imported.stats.imported === 2)
  await dialog.getByLabel('企微资料链接').fill('https://drive.weixin.qq.com/s?k=synthetic_file')
  await dialog.getByRole('button', { name: '读取预览', exact: true }).click()
  const disk = dialog.locator('.preview').filter({ hasText: '合成微盘巡检.txt' }).first()
  await disk.getByText('读取完成，待确认', { exact: true }).waitFor()
  check('微盘原件下载完成后才显示可确认')
  await dialog.getByLabel('企微资料链接').fill('https://doc.weixin.qq.com/doc/d3_denied')
  await dialog.getByRole('button', { name: '读取预览', exact: true }).click()
  const denied = dialog.locator('.preview').filter({ hasText: '需要开通搜索与获取文档内容' }).first()
  await denied.getByText('未完成', { exact: true }).waitFor()
  check('未授权时显示具体原因与官方入口', await denied.getByRole('link', { name: '前往企业微信开通此项功能' }).count() === 1)
  await page.screenshot({ path: path.join(OUT, 'desktop.png'), fullPage: true, animations: 'disabled' })
  await dialog.getByRole('tab', { name: '搜索我的资料' }).click()
  await dialog.getByLabel('企微资料关键词').fill('巡检')
  await dialog.getByRole('button', { name: '搜索', exact: true }).click()
  await dialog.getByRole('cell', { name: '合成搜索巡检手册', exact: true }).waitFor()
  check('可按关键词搜索本人在线资料并勾选')
  await page.reload(); await page.getByRole('button', { name: '从企业微信导入', exact: true }).click()
  await dialog.locator('.preview').first().waitFor()
  check('刷新后恢复本人的读取进度与预览')
  const mobile = await pageFor(390), mobileDialog = mobile.getByRole('dialog')
  await mobileDialog.getByLabel('企微资料链接').waitFor()
  await mobile.waitForTimeout(400)
  const box = await mobileDialog.boundingBox()
  check('手机可以打开企微导入入口，弹窗不溢出', box.width <= 390 && box.x >= 0)
  await mobile.screenshot({ path: path.join(OUT, 'mobile.png'), animations: 'disabled' })
  check('浏览器无运行错误', !errors.length)
  console.log(`${checks} checks passed; synthetic CLI fixtures; evidence: ${OUT}`)
} catch (e) {
  for (const [i, ctx] of browser.contexts().entries()) if (ctx.pages()[0]) await ctx.pages()[0].screenshot({ path: path.join(OUT, `failure-${i}.png`), fullPage: true })
  throw e
} finally { await browser.close() }
