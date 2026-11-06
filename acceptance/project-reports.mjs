// Read-only acceptance for an already imported dataset. AUTH_FILE contains a short-lived token.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import crypto from 'node:crypto'
import { chromium } from 'playwright'

const base = process.env.WEB || 'http://127.0.0.1:18108'
if (!process.env.AUTH_FILE || !process.env.OUT) throw new Error('Set AUTH_FILE and OUT to private local paths')
const { token } = JSON.parse(await fs.readFile(process.env.AUTH_FILE, 'utf8'))
const out = path.resolve(process.env.OUT)
await fs.mkdir(out, { recursive: true })
const headers = { Authorization: `Bearer ${token}` }
async function api(url) {
  const response = await fetch(base + '/api/v1' + url, { headers })
  assert.equal(response.status, 200)
  const result = await response.json()
  assert.equal(result.code, 0)
  return result.data
}
const sources = await api('/reports/project-sources?size=100')
assert.ok(sources.total > 0 && sources.total <= 100)
assert.equal(sources.items.length, sources.total)
const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) })
const context = await browser.newContext({ viewport: { width: 1600, height: 1050 }, acceptDownloads: true })
await context.addInitScript(({ token }) => localStorage.setItem('jc_token', token), { token })
const page = await context.newPage()
const errors = []
page.on('pageerror', e => errors.push(e.message))
try {
  await page.goto(base + '/profit', { waitUntil: 'networkidle' })
  await page.getByText('历史项目报表', { exact: true }).waitFor()
  const buttons = page.getByRole('button', { name: '查看明细', exact: true })
  assert.equal(await buttons.count(), sources.total)
  await page.screenshot({ path: path.join(out, 'profit-sources.png') })

  const index = Math.max(0, sources.items.findIndex(r => r.verification.held_rows > 0))
  const source = await api(`/reports/project-sources/${sources.items[index].id}`)
  await buttons.nth(index).click()
  const drawer = page.locator('.el-drawer')
  await drawer.getByText(source.source_name, { exact: true }).waitFor()
  if (source.verification.held_rows > 0) {
    assert.equal(await drawer.getByText('暂缓待核对', { exact: true }).count(), source.verification.held_rows)
  }
  await drawer.getByRole('tab', { name: /差异与待核对/ }).click()
  await drawer.screenshot({ path: path.join(out, 'source-review.png'), animations: 'disabled' })
  await drawer.getByRole('tab', { name: '原始表格与公式', exact: true }).click()
  await drawer.getByText('B', { exact: true }).first().waitFor()
  const downloadPromise = page.waitForEvent('download')
  await drawer.getByRole('link', { name: /下载原始报表/ }).click()
  const download = await downloadPromise
  const downloaded = path.join(out, 'downloaded-source.xlsx')
  await download.saveAs(downloaded)
  const hash = crypto.createHash('sha256').update(await fs.readFile(downloaded)).digest('hex')
  assert.equal(hash, source.source_sha256)
  const anon = await fetch(new URL(source.file.url, base))
  assert.equal(anon.status, 401)
  await drawer.locator('.el-drawer__close-btn').click()

  const contract = source.contracts[0]
  await page.goto(base + `/contracts/${contract.id}`, { waitUntil: 'networkidle' })
  await page.getByText('历史项目报表', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '查看明细', exact: true }).count(), 1)
  await page.goto(base + `/projects/${contract.project_code}`, { waitUntil: 'networkidle' })
  await page.getByText('历史项目报表', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '查看明细', exact: true }).count(), 1)

  const group = sources.items.find(r => r.contracts.length > 1)
  if (group) {
    const profit = await api('/reports/profit?start=2026-01-01&end=2026-09-30')
    const row = profit.rows.find(r => r.group_codes?.includes(group.primary_project_code))
    assert.ok(row && row.group_codes.length > 1)
    const drill = await api(`/reports/profit/${group.primary_project_code}?start=2026-01-01&end=2026-09-30`)
    assert.equal(drill.detail.contracts.length, group.contracts.length)
    await page.goto(base + '/profit', { waitUntil: 'networkidle' })
    await page.locator('tr.el-table__row').filter({ hasText: row.name }).first().click()
    await page.getByRole('tab', { name: `合同 ${group.contracts.length}`, exact: true }).click()
    await page.locator('.drill').filter({ has: page.getByRole('tab', { name: `合同 ${group.contracts.length}`, exact: true }) }).screenshot({ path: path.join(out, 'combined-project.png') })
  }
  assert.deepEqual(errors, [])
  const result = { reports: sources.total, source_drawer: true, held_rows_visible: true,
    contract_source_link: true, project_source_link: true, combined_contract_drill: true,
    authenticated_download_hash_matches: true, anonymous_download_denied: true, page_errors: errors }
  await fs.writeFile(path.join(out, 'browser-results.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
} finally {
  await browser.close()
}
