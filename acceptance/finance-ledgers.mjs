import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
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

function xlsx(sheets, { emptyFill = false } = {}) {
  return execFileSync(process.env.FINANCE_TEST_PYTHON || 'python3', ['-c', `
import io, json, sys
from zipfile import ZipFile, ZIP_DEFLATED
from openpyxl import Workbook
workbook = Workbook()
workbook.remove(workbook.active)
for name, rows in json.load(sys.stdin).items():
    sheet = workbook.create_sheet(name)
    for row in rows:
        sheet.append(row)
buffer = io.BytesIO()
workbook.save(buffer)
if sys.argv[1] == 'empty-fill':
    exported = io.BytesIO()
    with ZipFile(buffer) as original, ZipFile(exported, 'w', ZIP_DEFLATED) as target:
        for name in original.namelist():
            content = original.read(name)
            if name == 'xl/styles.xml':
                assert b'<fill><patternFill/></fill>' in content
                content = content.replace(b'<fill><patternFill/></fill>', b'<fill/>', 1)
            target.writestr(name, content)
    buffer = exported
sys.stdout.buffer.write(buffer.getvalue())
`, emptyFill ? 'empty-fill' : 'standard'], { input: JSON.stringify(sheets) })
}

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
  const mimeType = filename.endsWith('.xlsx') ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'text/csv'
  await dialog.locator('input[type=file]').setInputFiles({ name: filename, mimeType, buffer: Buffer.from(content) })
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

await step('FIN-01', '兼容在线表格空样式，银行收入 XLSX 直接核对', async () => {
  await visit('/receivables?ledger=bank_receipt', '出纳日记账')
  const directions = screen.getByRole('group', { name: '收支方向' })
  assert.equal(await directions.getByRole('button', { name: '收入', exact: true }).getAttribute('aria-pressed'), 'true')
  assert.equal((await existing('bank_receipt')).total, 0)
  const columns = ['序号', '收款日期', '收款银行', '汇款公司', '收入金额', '摘要', '项目']
  const dialog = await preview('测试_在线表格导出_银行收款.xlsx', xlsx({ 银行收入: [
    ['收款记录明细表'],
    columns,
    [1, '2026/11/4', '测试银行尾号 2208', '示例技术公司', 3680.50, '首批设备款', '校园设备项目'],
    [2, '2026/11/5', '测试银行尾号 2208', '示例教育中心', 15620.90, '阶段款', '实验室改造'],
    [3, '2026/11/6', '测试银行尾号 6702', '示例文化中心', 865.35, '维护补款', '校园服务'],
  ] }, { emptyFill: true }))
  assert.equal(previewResponse.groups[0].method, 'rule')
  assert.equal(previewResponse.counts.ready, 3)
  assert.equal((await existing('bank_receipt')).total, 0)
  const previewEvidence = await capture(screen, '00-在线表格导出-银行收入核对')
  assert.equal((await confirm(dialog)).inserted, 3)
  await screen.getByText('示例技术公司', { exact: true }).waitFor()
  assert.equal((await existing('bank_receipt')).totals.inflow, '20166.75')
  const region = screen.getByRole('region', { name: '银行收入日记账' })
  assert.deepEqual(await region.getByRole('columnheader').allTextContents(), [...columns, '操作'])
  return { evidence: [previewEvidence, await capture(screen, '01-银行日记账-收入')] }
})

await step('FIN-02', '开票台账在前，二维码入口停用，年份和月份可切换', async () => {
  await visit('/receivables', '开票回款')
  const tabs = screen.getByRole('navigation', { name: '财务账表' })
  assert.deepEqual(await tabs.getByRole('button').allTextContents(), ['开票台账', '收款记录明细'])
  assert.equal(await tabs.getByRole('button', { name: '开票台账' }).getAttribute('aria-current'), 'page')
  await tabs.getByRole('button', { name: '收款记录明细' }).click()
  const columns = ['开票日期', '开票单位', '名称', '收款日期', '收款金额', '备注']
  const extra = await preview('测试_已收账款明细.xlsx', xlsx({ 已收账款明细: [
    ['已收账款明细表'],
    ['编制单位：示例教育科技公司'],
    columns,
    [null, null, '已收款合计', null, 86850.90],
    ['2025/11/15', '示例一部', '示例旧年收款', '2025/12/15', 900],
    ['2025/12/25', '示例一部', '示例教育中心—实验室改造第一期项目款', '2026/3/15', 54600.00, '跨年到账'],
    ['2026/6/8', '示例二部', '示例中学—智慧校园设备及安装服务款', '2026/6/23', 23600.90, '阶段款'],
    ['2026/10/20', '示例一部', '示例图书馆—图书流通系统升级', '2026/11/5', 7750.00, '尾款'],
  ] }))
  assert.equal(previewResponse.groups[0].method, 'rule')
  assert.equal((await confirm(extra)).inserted, 4)
  const region = screen.getByRole('region', { name: '收款记录明细' })
  assert.deepEqual(await region.getByRole('columnheader').allTextContents(), [...columns, '操作'])
  await region.getByRole('combobox', { name: '按年份筛选' }).click()
  const annual = screen.waitForResponse((res) => res.url().includes('/entries/collection_receipt?') && new URL(res.url()).searchParams.get('period') === '2026')
  await screen.getByRole('gridcell', { name: '2026', exact: true }).click()
  const result = (await (await annual).json()).data
  assert.equal(result.total, 3)
  assert.equal(result.items.find((item) => item.values.name.includes('第一期项目款')).values.invoice_date, '2025-12-25')
  await region.getByText('示例教育中心—实验室改造第一期项目款', { exact: true }).waitFor()
  await screen.getByRole('gridcell', { name: '2026', exact: true }).waitFor({ state: 'hidden' })
  assert.equal(await region.getByText('示例旧年收款', { exact: true }).count(), 0)
  const evidence = await capture(screen, '02-开票回款-全年收款记录')
  await region.getByRole('combobox', { name: '按月份筛选' }).press('Enter')
  const monthly = screen.waitForResponse((res) => res.url().includes('/entries/collection_receipt?') && new URL(res.url()).searchParams.get('period') === '2026-03')
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
  const directions = screen.getByRole('group', { name: '收支方向' })
  await directions.getByRole('button', { name: '收入', exact: true }).click()
  await screen.getByText('示例技术公司', { exact: true }).waitFor()
  await directions.getByRole('button', { name: '支出', exact: true }).click()
  await screen.getByRole('region', { name: '银行支出日记账' }).waitFor()
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

await step('FIN-04', '图五标准现金 XLSX，收入、支出、结转和零发生', async () => {
  await visit('/journal?ledger=cash_journal', '出纳日记账')
  const columns = ['序号', '日期', '对方科目', '申请编号', '摘要', '收入金额', '支付金额', '结余金额', '备注']
  const dialog = await preview('测试_现金日记账.xlsx', xlsx({ 现金日记账: [
    columns,
    [1, null, null, null, '上月结转', null, null, 810.59],
    [2, '2026/11/2', '示例银行', null, '备用金补充', 5000, null, 5810.59],
    [3, '2026/11/7', '示例员工甲', '202611070034', '报销交通费', null, 125.30, 5685.29, '凭证齐全'],
    [4, '2026/11/8', '示例员工乙', '202611080002', '日常采购', null, 2000, 3685.29],
    [5, '2026/11/8', '内部转记', null, '零发生核对行', null, 0, 3685.29],
  ] }))
  assert.equal(previewResponse.groups[0].method, 'rule')
  await dialog.getByText('有核对提示', { exact: true }).waitFor()
  await dialog.getByRole('button', { name: '零发生 1', exact: true }).waitFor()
  assert.equal((await confirm(dialog)).inserted, 5)
  await screen.getByText('上月结转', { exact: true }).waitFor()
  const result = await existing('cash_journal', { period: '2026-11' })
  assert.equal(result.totals.inflow, '5000.00')
  assert.equal(result.totals.outflow, '2125.30')
  assert.equal(result.items[0].source.row, 2)
  const region = screen.getByRole('region', { name: '现金日记账' })
  assert.deepEqual(await region.getByRole('columnheader').allTextContents(), [...columns, '操作'])
  const overview = await capture(screen, '05-现金日记账-全部')
  const directions = screen.getByRole('group', { name: '收支方向' })
  const income = screen.waitForResponse((res) => res.url().includes('/entries/cash_journal?') && new URL(res.url()).searchParams.get('direction') === 'income')
  await directions.getByRole('button', { name: '收入', exact: true }).click()
  assert.equal((await (await income).json()).data.total, 1)
  await region.getByText('备用金补充', { exact: true }).waitFor()
  assert.equal(await region.getByText('零发生核对行', { exact: true }).count(), 0)
  const inflow = await capture(screen, '05b-现金日记账-收入')
  const expense = screen.waitForResponse((res) => res.url().includes('/entries/cash_journal?') && new URL(res.url()).searchParams.get('direction') === 'expense')
  await directions.getByRole('button', { name: '支出', exact: true }).click()
  assert.equal((await (await expense).json()).data.total, 2)
  await region.getByText('日常采购', { exact: true }).waitFor()
  assert.equal(await region.getByText('上月结转', { exact: true }).count(), 0)
  return { evidence: [overview, inflow, await capture(screen, '05c-现金日记账-支出')] }
})

await step('FIN-05', '图一 XLSX 可直接导入无票号开票台账，跳过合计', async () => {
  await visit('/receivables?ledger=invoice', '开票回款')
  const columns = ['开票日期', '开票单位', '客户', '项目名称', '借方', '备注']
  const dialog = await preview('测试_开票台账.xlsx', xlsx({ 开票台账: [
    ['编制单位：示例教育科技公司'],
    columns,
    [null, null, '应收账款合计', null, 48515.35],
    ['2026/11/3', '示例一部', '示例教育中心', '实验室改造设备项目', 17550.00, '一期款'],
    ['2026/11/5', '示例二部', '示例文化中心', '文化中心年度运维服务', 965.35, '年度服务'],
    ['2026/11/6', '示例一部', '示例职业技术学校', '实训教室设备更新', 30000.00, '设备款'],
  ] }))
  assert.equal(previewResponse.groups[0].method, 'rule')
  assert.equal((await confirm(dialog)).inserted, 3)
  await screen.getByText('实训教室设备更新', { exact: true }).waitFor()
  assert.deepEqual(await screen.getByRole('region', { name: '开票台账' }).getByRole('columnheader').allTextContents(), [...columns, '操作'])
  assert.equal((await existing('invoice')).totals.inflow, '48515.35')
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
  await visit('/journal?ledger=bank_receipt', '出纳日记账')
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

await step('FIN-10', '真实小聚整理非标准现金 XLSX，来源不明的金额待人工核对', async () => {
  await visit('/journal?ledger=cash_journal', '出纳日记账')
  const dialog = await preview('测试_零用金登记_非标准.xlsx', xlsx({ 十月零用金: [
    ['2026 年 10 月零用金备查'],
    ['是哪一天', '发生什么', '资金怎么走', '实际动了多少', '还剩多少'],
    [null, '上月结转', null, null, 1000],
    ['2026/10/2', '补充零用金', '补入备用金', 850, 1850],
    ['2026/10/3', '购买办公耗材', '从备用金付出', 350, 1500],
    ['2026/10/4', '待核对原始凭证', '未填写', 90, null],
  ] }))
  assert.equal(previewResponse.groups[0].method, 'xiaoju', JSON.stringify(previewResponse.groups))
  assert.deepEqual(previewResponse.rows.map((row) => row.cash_category), ['opening', 'income', 'expense', 'unknown'])
  assert.equal(previewResponse.counts.ready, 3)
  assert.equal((await existing('cash_journal', { period: '2026-10' })).total, 0)
  const evidence = await capture(screen, '11-小聚整理现金混合表')
  await dialog.getByRole('button', { name: '待核对 1', exact: true }).click()
  const row = dialog.getByRole('row').filter({ hasText: '待核对原始凭证' })
  await row.getByRole('button', { name: '补充', exact: true }).click()
  const edit = screen.getByRole('dialog', { name: '核对源数据' })
  await edit.getByRole('textbox', { name: '支付金额' }).fill('90.00')
  await edit.getByRole('button', { name: '保存并校验' }).click()
  await dialog.getByRole('button', { name: '确认导入 4 笔', exact: true }).waitFor()
  assert.equal((await confirm(dialog)).inserted, 4)
  const result = await existing('cash_journal', { period: '2026-10' })
  assert.deepEqual(result.totals, { inflow: '850.00', outflow: '440.00', fee: '0.00' })
  return { evidence }
})

await step('FIN-11', '确实无法读取的 Excel 显示文件名、处理办法和问题编号', async () => {
  await visit('/journal?ledger=bank_receipt', '出纳日记账')
  await screen.getByRole('button', { name: '导入财务账表', exact: true }).click()
  const dialog = screen.getByRole('dialog', { name: '导入财务账表' })
  const pending = screen.waitForResponse((res) => res.url().endsWith(`${base}/preview`))
  await dialog.locator('input[type=file]').setInputFiles({ name: '测试_无法打开的文件.xlsx',
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('not a workbook') })
  const response = await pending
  assert.equal(response.status(), 400)
  const result = await response.json()
  const alert = dialog.getByRole('alert')
  await alert.getByText(/另存为 \.xlsx/).waitFor()
  await alert.getByText('测试_无法打开的文件.xlsx', { exact: true }).waitFor()
  await alert.getByText(`问题编号 ${result.trace_id}`, { exact: true }).waitFor()
  return { evidence: await capture(screen, '12-无法读取时提示文件名和问题编号') }
})

await browser.close()
const results = save()
if (results.failed) process.exitCode = 1
