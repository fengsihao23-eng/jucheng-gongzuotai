// 验收工具：登录、API 调用（统一包解包）、截图、证据记录。
import fs from 'node:fs'
import path from 'node:path'

export const WEB = process.env.WEB || 'http://localhost:5180'
export const API = process.env.API || 'http://localhost:8100'
export const PWD = process.env.DEMO_PASSWORD || 'Jc@2026'
export const OUT = path.resolve(process.env.OUT || './evidence')
fs.mkdirSync(path.join(OUT, 'screens'), { recursive: true })

const results = []
const tokens = {}

export async function token(userid) {
  if (!tokens[userid]) {
    const r = await fetch(`${API}/api/v1/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: userid, password: PWD }) }).then((r) => r.json())
    if (r.code !== 0) throw new Error(`login ${userid}: ${r.message}`)
    tokens[userid] = r.data.token
  }
  return tokens[userid]
}

export async function call(userid, method, url, body, { raw = false } = {}) {
  const t = userid ? await token(userid) : null
  if ((method === 'GET' || method === 'DELETE') && body) {
    const q = new URLSearchParams(Object.entries(body).filter(([, v]) => v !== undefined && v !== null))
    url += (url.includes('?') ? '&' : '?') + q
    body = undefined
  }
  const r = await fetch(`${API}/api/v1${url}`, {
    method, headers: { 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  const j = await r.json()
  if (raw) return { status: r.status, ...j }
  if (j.code !== 0) throw new Error(`${method} ${url} → ${j.code} ${j.message}`)
  return j.data
}

export async function upload(userid, url, filePath, fields = {}) {
  const fd = new FormData()
  fd.append('file', new Blob([fs.readFileSync(filePath)]), path.basename(filePath))
  Object.entries(fields).forEach(([k, v]) => fd.append(k, v))
  const r = await fetch(`${API}/api/v1${url}`, { method: 'POST', headers: { Authorization: `Bearer ${await token(userid)}` }, body: fd })
  const j = await r.json()
  if (j.code !== 0) throw new Error(`upload ${url} → ${j.code} ${j.message}`)
  return j.data
}

/** 记录一条验收项：id 对应 PRD 条款，pass 布尔，evidence 文本/数据 */
export function check(id, title, pass, evidence) {
  results.push({ id, title, pass: !!pass, evidence })
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${id}  ${title}${pass ? '' : '  ← ' + JSON.stringify(evidence).slice(0, 300)}`)
}

export async function step(id, title, fn) {
  try {
    const r = await fn()
    check(id, title, r?.pass ?? true, r?.evidence ?? r)
  } catch (e) {
    check(id, title, false, String(e.stack || e))
  }
}

export async function loginPage(browser, userid, viewport = { width: 1440, height: 900 }) {
  const ctx = await browser.newContext({ viewport, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' })
  const t = await token(userid)
  await ctx.addInitScript((tk) => localStorage.setItem('jc_token', tk), t)
  const page = await ctx.newPage()
  page.on('pageerror', (e) => results.push({ id: 'console', title: `页面错误 ${userid}`, pass: false, evidence: String(e) }))
  return page
}

export async function shot(page, name) {
  const p = path.join(OUT, 'screens', `${name}.png`)
  await page.screenshot({ path: p, fullPage: true })
  return `screens/${name}.png`
}

export function save() {
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify({ at: new Date().toISOString(), results }, null, 2))
  const fail = results.filter((r) => !r.pass)
  console.log(`\n${results.length - fail.length}/${results.length} passed`)
  return { total: results.length, failed: fail.length }
}
