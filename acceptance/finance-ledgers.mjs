import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { WEB, call, loginPage, save, shot, step } from './lib.mjs'

const user = 'chenjing'
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' })
const screen = await loginPage(browser, user, { width: 1680, height: 960 })
const base = '/finance-ledgers'
const existing = (kind, options) => call(user, 'GET', `${base}/entries/${kind}`, options)
async function capture(page, name) {
  await page.waitForFunction(() => !document.querySelector('.el-message, .dialog-fade-enter-active, .dialog-fade-leave-active'))
  return shot(page, name)
}
let previewResponse

async function visit(path, heading) {
  const before = screen.waitForResponse((res) => res.ok() && res.url().includes(`${base}/entries/`))
  await screen.goto(`${WEB}${path}`)
  await screen.getByRole('heading', { name: heading, exact: true }).waitFor()
  await screen.getByRole('button', { name: '导入财务账表', exact: true }).waitFor()
  await before
}

async function preview(filename, content) {
  await screen.getByRole('button', { name: '导入财务账表', exact: true }).click()
  const dialog = screen.getByRole('dialog', { name: '导入财务账表' })
  await dialog.locator('input[type=file]').setInputFiles({ name: filename, mimeType: 'text/csv', buffer: Buffer.from(content) })
  const pending = screen.waitForResponse((res) => res.url().endsWith(`${base}/preview`), { timeout: 105000 })
  await dialog.getByRole('button', { name: '整理并预览' }).click()
  const result = await (await pending).json()
  assert.equal(result.code, 0, result.message)
  previewResponse = result.data
  const review = screen.getByRole('dialog', { name: '核对导入预览' })
  await review.waitFor()
  return review
}

async function confirm(dialog) {
  const pending = screen.waitForResponse((res) => res.url().endsWith('/confirm') && res.url().includes(base))
  await dialog.getByRole('button', { name: '确认导入' }).click()
  const result = await (await pending).json()
  assert.equal(result.code, 0, result.message)
  await dialog.waitFor({ state: 'hidden' })
  return result.data
}

await step('FIN-01', '真实小聚整理非标准银行表头，预览时尚无入账', async () => {
  await visit('/receivables', '开票回款')
  assert.equal((await existing('bank_receipt')).total, 0)
  const dialog = await preview('测试_渠道到账明细.csv', `哪天到账,钱进了哪个户,谁转来的,进了多少钱,归到哪项,随款说明
2026/11/4,测试银行尾号 2208,示例技术公司,3680.50,校园设备项目,首批设备款
2026/11/5,测试银行尾号 2208,示例教育中心,15620.90,实验室改造,阶段款
2026/11/6,测试银行尾号 6702,示例文化中心,865.35,校园服务,维护补款
2026/11/7,测试银行尾号 6702,示例服务公司,,校园服务,待核对金额`)
  assert.equal(previewResponse.groups[0].method, 'xiaoju', JSON.stringify(previewResponse.groups))
  assert.equal(previewResponse.counts.ready, 3)
  assert.equal(previewResponse.counts.error, 1)
  assert.equal((await existing('bank_receipt')).total, 0)
  const evidence = await capture(screen, '01-小聚整理与逐行确认')
  assert.equal((await confirm(dialog)).inserted, 3)
  await screen.getByText('示例技术公司', { exact: true }).waitFor()
  assert.equal((await existing('bank_receipt')).totals.inflow, '20166.75')
  return { evidence: [evidence, await capture(screen, '02-开票回款-银行收款明细')] }
})

await step('FIN-02', '二维码手续费和实收校验，简式收款自动识别账户', async () => {
  await screen.getByRole('button', { name: '二维码收款明细', exact: true }).click()
  const dialog = await preview('测试账户 6039 全付通收款记录明细表.csv', `序号,收款日期,交易金额,手续费,实收金额,项目
1,2026/11/4,2280,5.24,2274.76,体育用品
2,2026/11/5,900,2.07,897.93,校园课程
3,2026/11/6,300,0.69,299.31,校园课程`)
  assert.equal((await confirm(dialog)).inserted, 3)
  const simple = await preview('测试账户 7791 二维码收款明细表.csv', `序号,收款日期,实收金额,项目
1,2026/11/5,11371.50,设备维修
2,2026/11/8,985.00,活动器材`)
  assert.equal((await confirm(simple)).inserted, 2)
  assert.equal((await existing('qr_receipt')).totals.inflow, '15828.50')
  await screen.getByText('测试账户 7791', { exact: true }).first().waitFor()
  return { evidence: await capture(screen, '03-开票回款-二维码收款明细') }
})

await step('FIN-03', '银行支出和费用归集写入独立台账', async () => {
  await visit('/expenses', '报销归集')
  const dialog = await preview('测试_银行支出日记账.csv', `序号,日期,汇款公司,摘要,收款方资料,金额,申请人,审批编号,备注
1,2026/11/5,示例银行 2208,教室布线及设备首款,示例工程公司,15000.00,示例员工甲,202611050001,按进度支付
2,2026/11/6,示例银行 2208,实验室设备配件,示例设备公司,14150.00,示例员工乙,202611060002,已付款
3,2026/11/9,示例银行 6702,办公用品采购,示例办公用品公司,1250.80,示例员工丙,202611090003,月结`)
  assert.equal((await confirm(dialog)).inserted, 3)
  const row = screen.getByRole('row').filter({ hasText: '示例设备公司' })
  await row.getByRole('button', { name: '归集', exact: true }).click()
  const allocation = screen.getByRole('dialog', { name: '归集支出' })
  await allocation.getByRole('textbox', { name: '费用归属' }).fill('实验室改造')
  await allocation.getByRole('button', { name: '保存归属' }).click()
  await allocation.waitFor({ state: 'hidden' })
  await row.getByText('实验室改造', { exact: true }).waitFor()
  assert.equal((await existing('bank_expense', { project: 'unassigned' })).total, 2)
  return { evidence: await capture(screen, '04-报销归集-银行支出日记账') }
})

await step('FIN-04', '现金结转只保留余额，来源行号可溯源', async () => {
  await screen.getByRole('button', { name: '现金日记账', exact: true }).click()
  const dialog = await preview('测试账户 3302 现金日记账.csv', `序号,日期,对方科目,申请编号,摘要,收入金额,支付金额,结余金额,备注
1,,,,上月结转,,,810.59,
2,2026/11/2,示例银行,,备用金补充,5000,,5810.59,
3,2026/11/7,示例员工甲,202611070034,报销交通费,,125.30,5685.29,凭证齐全
4,2026/11/8,示例员工乙,202611080002,日常采购,,2000,3685.29,`)
  await dialog.getByText('有核对提示', { exact: true }).waitFor()
  assert.equal((await confirm(dialog)).inserted, 4)
  await screen.getByText('上月结转', { exact: true }).waitFor()
  const result = await existing('cash_journal', { period: '2026-11' })
  assert.equal(result.totals.inflow, '5000.00')
  assert.equal(result.totals.outflow, '2125.30')
  assert.equal(result.items[0].source.row, 2)
  return { evidence: await capture(screen, '05-报销归集-现金日记账') }
})

await step('FIN-05', '开票台账登记原表发票号，保留前导零', async () => {
  await visit('/receivables?ledger=invoice', '开票回款')
  const dialog = await preview('测试_开票台账.csv', `序号,开票日期,发票号码,购方名称,价税合计,备注
1,2026/11/5,00023100001992,示例教育中心,17550.00,一期款
2,2026/11/6,00023100001993,示例文化中心,965.35,年度服务`)
  assert.equal((await confirm(dialog)).inserted, 2)
  await screen.getByText('00023100001992', { exact: true }).waitFor()
  return { evidence: await capture(screen, '06-开票回款-开票台账') }
})

await step('FIN-06', '手机可打开导入弹窗', async () => {
  const mobile = await loginPage(browser, user, { width: 390, height: 844 })
  await mobile.goto(`${WEB}/receivables`)
  await mobile.getByRole('button', { name: '导入财务账表', exact: true }).click()
  const dialog = mobile.getByRole('dialog', { name: '导入财务账表' })
  await dialog.getByText('整理并预览', { exact: true }).waitFor()
  const bounds = await dialog.boundingBox()
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390)
  return { evidence: await capture(mobile, '07-手机-导入财务账表') }
})

await browser.close()
const results = save()
if (results.failed) process.exitCode = 1
