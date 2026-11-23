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
  const pending = screen.waitForResponse((res) => res.url().endsWith(`${base}/preview`), { timeout: 105000 })
  await dialog.locator('input[type=file]').setInputFiles({ name: filename, mimeType: 'text/csv', buffer: Buffer.from(content) })
  const result = await (await pending).json()
  assert.equal(result.code, 0, result.message)
  previewResponse = result.data
  const review = screen.getByRole('dialog', { name: '核对导入数据' })
  await review.waitFor()
  return review
}

async function confirm(dialog) {
  const pending = screen.waitForResponse((res) => res.url().endsWith('/confirm') && res.url().includes(base))
  await dialog.getByRole('button', { name: /^确认导入 \d+ 笔$/ }).click()
  const result = await (await pending).json()
  assert.equal(result.code, 0, result.message)
  await dialog.waitFor({ state: 'hidden' })
  const receipt = screen.getByRole('dialog', { name: '导入完成' })
  await receipt.getByText(`已入账 ${result.data.inserted} 笔`, { exact: true }).waitFor()
  await receipt.getByRole('button', { name: '完成', exact: true }).click()
  await receipt.waitFor({ state: 'hidden' })
  return result.data
}

await step('FIN-01', '真实小聚整理非标准银行表头，预览时尚无入账', async () => {
  await visit('/receivables?ledger=bank_receipt', '开票回款')
  assert.equal((await existing('bank_receipt')).total, 0)
  const dialog = await preview('测试_渠道到账明细.csv', `哪天到账,钱进了哪个户,谁转来的,进了多少钱,归到哪项,随款说明
2026/11/4,测试银行尾号 2208,示例技术公司,3680.50,校园设备项目,首批设备款
2026/11/5,测试银行尾号 2208,示例教育中心,15620.90,实验室改造,阶段款
2026/11/6,测试银行尾号 6702,示例文化中心,865.35,校园服务,维护补款
2026/11/7,测试银行尾号 6702,示例服务公司,,校园服务,待核对金额`)
  assert.equal(previewResponse.groups[0].method, 'xiaoju', JSON.stringify(previewResponse.groups))
  assert.equal(previewResponse.counts.ready, 3)
  const missingAmount = previewResponse.rows.find((row) => row.row === 5)
  assert.equal(missingAmount?.status, 'error')
  assert.ok(missingAmount.errors.net)
  assert.equal(previewResponse.counts.error, 1)
  assert.equal((await existing('bank_receipt')).total, 0)
  await dialog.getByRole('button', { name: /^待补充 / }).click()
  await dialog.getByRole('button', { name: '补充', exact: true }).first().waitFor()
  assert.equal(await dialog.getByText('示例技术公司', { exact: true }).count(), 0)
  await dialog.getByRole('button', { name: /^全部 / }).click()
  const evidence = await capture(screen, '01-小聚整理与逐行确认')
  assert.equal((await confirm(dialog)).inserted, 3)
  await screen.getByText('示例技术公司', { exact: true }).waitFor()
  assert.equal((await existing('bank_receipt')).totals.inflow, '20166.75')
  return { evidence: [evidence, await capture(screen, '01b-银行收款入账后')] }
})

await step('FIN-02', '开票台账在前，二维码入口停用，年份和月份可切换', async () => {
  await visit('/receivables', '开票回款')
  const tabs = screen.getByRole('navigation', { name: '财务账表' })
  assert.deepEqual(await tabs.getByRole('button').allTextContents(), ['开票台账', '收款记录明细'])
  assert.equal(await tabs.getByRole('button', { name: '开票台账' }).getAttribute('aria-current'), 'page')
  await tabs.getByRole('button', { name: '收款记录明细' }).click()
  const extra = await preview('测试_跨年度收款.csv', `收款日期,收款银行,汇款公司,收入金额
2025/12/15,测试银行尾号 2208,示例旧年收款,900
2026/3/15,测试银行尾号 2208,示例本年三月款,1400`)
  assert.equal((await confirm(extra)).inserted, 2)
  const region = screen.getByRole('region', { name: '收款记录明细' })
  await region.getByRole('combobox', { name: '按年份筛选' }).click()
  const annual = screen.waitForResponse((res) => res.url().includes('/entries/bank_receipt?') && new URL(res.url()).searchParams.get('period') === '2026')
  await screen.getByRole('gridcell', { name: '2026', exact: true }).click()
  const result = (await (await annual).json()).data
  assert.equal(result.total, 4)
  await region.getByText('示例本年三月款', { exact: true }).waitFor()
  await screen.getByRole('gridcell', { name: '2026', exact: true }).waitFor({ state: 'hidden' })
  assert.equal(await region.getByText('示例旧年收款', { exact: true }).count(), 0)
  const evidence = await capture(screen, '02-开票回款-全年收款记录')
  await region.getByRole('combobox', { name: '按月份筛选' }).press('Enter')
  const monthly = screen.waitForResponse((res) => res.url().includes('/entries/bank_receipt?') && new URL(res.url()).searchParams.get('period') === '2026-03')
  await screen.getByRole('option', { name: '3 月', exact: true }).click()
  assert.equal((await (await monthly).json()).data.total, 1)
  return { evidence }
})

await step('FIN-03', '截图中的银行支出归出纳日记账，报销归集显示报销单', async () => {
  await screen.goto(`${WEB}/expenses`)
  await screen.getByRole('heading', { name: '报销归集', exact: true }).waitFor()
  await screen.getByText('审批单号', { exact: true }).first().waitFor()
  assert.equal(await screen.getByRole('button', { name: '银行支出日记账', exact: true }).count(), 0)
  const stat = screen.getByRole('combobox', { name: '报销统计周期' })
  await stat.press('Enter')
  const yearlyExpenses = screen.waitForResponse((res) => res.url().includes('/expenses/stats?') && new URL(res.url()).searchParams.get('end') === '2026-12-31')
  await screen.getByRole('option', { name: '按年', exact: true }).click()
  assert.equal((await (await yearlyExpenses).json()).data.start, '2026-01-01')
  const expenseEvidence = await capture(screen, '03-报销归集-全年统计')
  await visit('/journal', '出纳日记账')
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
  return { evidence: [expenseEvidence, await capture(screen, '04-出纳日记账-银行支出')] }
})

await step('FIN-04', '现金结转只保留余额，来源行号可溯源', async () => {
  await visit('/journal?ledger=cash_journal', '出纳日记账')
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
  return { evidence: await capture(screen, '05-出纳日记账-现金日记账') }
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
  await dialog.getByRole('button', { name: /选择文件或拖到这里/ }).waitFor()
  const bounds = await dialog.boundingBox()
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390)
  return { evidence: await capture(mobile, '07-手机-导入财务账表') }
})

await step('FIN-07', '取消与确认单条删除，校验合计和更正后的重复流水号', async () => {
  await visit('/receivables?ledger=bank_receipt', '开票回款')
  const file = `收款日期,收款银行,汇款公司,收入金额,流水号
2026/11/5,验收更正账户,验收单条更正客户,2150.90,FIN-QA-R001`
  assert.equal((await confirm(await preview('测试_单笔导入.csv', file))).inserted, 1)
  const row = screen.getByRole('row').filter({ hasText: '验收单条更正客户' })
  await row.getByRole('button', { name: '删除', exact: true }).click()
  const dialog = screen.getByRole('dialog', { name: '删除台账记录' })
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await dialog.waitFor({ state: 'hidden' })
  assert.equal((await existing('bank_receipt', { account: '验收更正账户' })).total, 1)
  await row.getByRole('button', { name: '删除', exact: true }).click()
  await dialog.getByRole('textbox', { name: '原因（选填）' }).fill('核对原表后发现录入金额有误')
  const evidence = await capture(screen, '08-单条删除确认')
  await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
  await row.waitFor({ state: 'hidden' })
  assert.equal((await existing('bank_receipt', { account: '验收更正账户' })).totals.inflow, '0.00')
  assert.equal((await confirm(await preview('测试_单笔导入_修正.csv', file.replace('2150.90', '1950.90')))).inserted, 1)
  assert.equal((await existing('bank_receipt', { account: '验收更正账户' })).totals.inflow, '1950.90')
  return { evidence }
})

await step('FIN-08', '整批撤销显示笔数，确认后只移除该文件对应记录', async () => {
  await visit('/journal', '出纳日记账')
  const file = `日期,汇款公司,摘要,收款方资料,金额,审批编号
2026/11/5,整批撤销验收账户,录错账套的材料款,验收撤销供应商甲,4180.00,202611050127
2026/11/6,整批撤销验收账户,录错账套的维修款,验收撤销供应商乙,600.30,202611060128`
  assert.equal((await confirm(await preview('测试_错导银行支出.csv', file))).inserted, 2)
  const row = screen.getByRole('region', { name: '银行支出日记账' }).getByRole('row').filter({ hasText: '验收撤销供应商甲' })
  await screen.getByRole('button', { name: '导入记录', exact: true }).click()
  const history = screen.getByRole('dialog', { name: '导入记录' })
  const imported = history.getByRole('row').filter({ hasText: '测试_错导银行支出.csv' })
  await imported.getByRole('button', { name: '撤销本批' }).click()
  const dialog = screen.getByRole('dialog', { name: '撤销整批导入' })
  await dialog.getByText('银行支出日记账 · 2 笔', { exact: true }).waitFor()
  const evidence = await capture(screen, '09-整批撤销确认')
  await dialog.getByRole('button', { name: '确认撤销', exact: true }).click()
  await imported.getByText('已移除', { exact: false }).waitFor()
  await row.waitFor({ state: 'hidden' })
  assert.equal((await existing('bank_expense', { account: '整批撤销验收账户' })).total, 0)
  assert.equal((await existing('bank_expense', { keyword: '示例设备公司' })).total, 1)
  return { evidence }
})


await step('FIN-09', '粘贴表格、统一补账户、保存待确认与继续核对', async () => {
  await visit('/journal?ledger=cash_journal', '出纳日记账')
  await screen.getByRole('button', { name: '导入财务账表', exact: true }).click()
  const upload = screen.getByRole('dialog', { name: '导入财务账表' })
  await upload.getByRole('button', { name: '粘贴表格', exact: true }).click()
  await upload.getByRole('textbox', { name: '粘贴账表内容' }).fill(`日期\t对方科目\t摘要\t收入金额\t支付金额\t结余金额
2026/2/2\t示例人员\t上半年备用金\t5000\t\t5000
2026/11/3\t示例人员\t下半年备用金\t1500\t\t6500`)
  await upload.getByRole('button', { name: '预览粘贴内容' }).click()
  const review = screen.getByRole('dialog', { name: '核对导入数据' })
  await review.getByRole('button', { name: '统一填写账户' }).click()
  await review.getByRole('textbox', { name: '统一账户名称' }).fill('粘贴验收现金账户')
  await review.getByRole('button', { name: '应用到账表' }).click()
  await review.getByRole('button', { name: '确认导入 2 笔', exact: true }).waitFor()
  await review.getByRole('button', { name: '稍后核对' }).click()
  await review.waitFor({ state: 'hidden' })
  await screen.getByRole('button', { name: '导入记录', exact: true }).click()
  const history = screen.getByRole('dialog', { name: '导入记录' })
  const draft = history.getByRole('row').filter({ hasText: '粘贴账表.txt' })
  await draft.getByText('待确认', { exact: true }).waitFor()
  const historyEvidence = await capture(screen, '10-导入记录-待确认和已入账')
  await draft.getByRole('button', { name: '继续核对' }).click()
  await review.getByRole('button', { name: '确认导入 2 笔', exact: true }).waitFor()
  assert.equal((await existing('cash_journal', { account: '粘贴验收现金账户' })).total, 0)
  assert.equal((await confirm(review)).inserted, 2)
  assert.equal((await existing('cash_journal', { account: '粘贴验收现金账户', period: '2026' })).totals.inflow, '6500.00')
  return { evidence: historyEvidence }
})

await browser.close()
const results = save()
if (results.failed) process.exitCode = 1
