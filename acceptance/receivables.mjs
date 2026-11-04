// 仅对本机隔离测试环境执行；会创建一张测试发票和一笔测试到账。
import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, call, loginPage, save, shot, step } from './lib.mjs'

for (const url of [API, WEB]) {
  assert.ok(['localhost', '127.0.0.1'].includes(new URL(url).hostname), '只允许本机隔离验收')
  const health = await fetch(`${url}/health`).then((r) => r.json())
  assert.equal(health.env, 'test')
  assert.equal(health.mode, 'mock')
}

const date = process.env.QA_DATE || '2026-10-10'
const suffix = Date.now().toString(36).toUpperCase()
const invoiceNo = `QA-${suffix}-FP`
const receiptRef = `QA-${suffix}-SK`
const customer = `验收客户 ${suffix}`
const period = date.slice(0, 7)
const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || undefined })
const finance = await loginPage(browser, 'chenjing')
const leader = await loginPage(browser, 'zhangzhenhua')

async function fill(dialog, label, value) {
  const input = dialog.locator('.el-form-item').filter({ hasText: label }).locator('input').first()
  await input.fill(value)
  await input.press('Tab')
}

try {
  await step('R-01', '企微模拟开票申请进入待确认，财务在页面确认实际开票', async () => {
    await call('admin', 'POST', '/admin/dicts', { dict_type: 'approval_template', dict_key: 'qa-approval-invoice',
      dict_value: { key: 'invoice', name: '验收开票审批', fields: { 客户: 'customer', 金额: 'amount' } } })
    const approval = await call('admin', 'POST', '/dev/mock/wecom-approval', { template: 'invoice', applicant: 'chenjing',
      fields: { 客户: customer, 金额: '600' } })
    assert.equal(approval.dispatch_status, 1)
    await finance.goto(`${WEB}/receivables?keyword=${encodeURIComponent(customer)}`)
    await finance.getByRole('row', { name: new RegExp(customer) }).getByRole('button', { name: '确认开票' }).click()
    const dialog = finance.getByRole('dialog', { name: '确认实际开票' })
    await fill(dialog, '发票号码', invoiceNo)
    await fill(dialog, '开票日期', date)
    const request = finance.waitForResponse((r) => r.url().endsWith('/issue') && r.request().method() === 'POST', { timeout: 10000 })
    await dialog.getByRole('button', { name: '确认', exact: true }).click()
    assert.equal((await (await request).json()).code, 0)
    await finance.getByRole('row', { name: new RegExp(invoiceNo) }).getByText('已开票', { exact: true }).waitFor()
    return { screenshot: await shot(finance, 'receivables-invoice') }
  })

  await step('R-02', '财务确认其他到账并部分核销，老板看板下钻核对余额', async () => {
    await finance.getByRole('tab', { name: '到账台账' }).click()
    await finance.getByRole('button', { name: '确认到账' }).click()
    const dialog = finance.getByRole('dialog', { name: '确认实际到账' })
    await dialog.getByText('合同回款', { exact: true }).click()
    await finance.getByRole('option', { name: '其他收款', exact: true }).click()
    await fill(dialog, '付款方', customer)
    await fill(dialog, '收款账户简称', '验收账户')
    await fill(dialog, '到账凭证号', receiptRef)
    await fill(dialog, '实际到账日', date)
    await fill(dialog, '金额（元）', '200')
    const request = finance.waitForResponse((r) => r.url().endsWith('/receivables/receipts') && r.request().method() === 'POST', { timeout: 10000 })
    await dialog.getByRole('button', { name: '确认', exact: true }).click()
    assert.equal((await (await request).json()).code, 0)
    await finance.getByRole('row', { name: new RegExp(receiptRef) }).getByRole('button', { name: '核销发票' }).click()
    const allocation = finance.getByRole('dialog', { name: '分配到账至发票' })
    await allocation.getByRole('button', { name: '增加发票' }).click()
    await allocation.getByRole('combobox').click()
    await finance.getByRole('option', { name: new RegExp(invoiceNo) }).click()
    await allocation.getByPlaceholder('核销金额（元）').fill('200')
    const posted = finance.waitForResponse((r) => r.url().endsWith('/allocations') && r.request().method() === 'PUT', { timeout: 10000 })
    await allocation.getByRole('button', { name: '确认', exact: true }).click()
    assert.equal((await (await posted).json()).code, 0)
    await leader.goto(`${WEB}/board?period=${period}`)
    await leader.getByText('当前已开票未收', { exact: true }).click()
    const row = leader.getByRole('row', { name: new RegExp(invoiceNo) })
    await row.getByText('¥400.00', { exact: true }).waitFor()
    assert.equal(await leader.getByRole('button', { name: '登记已开票' }).count(), 0)
    return { screenshot: await shot(leader, 'receivables-outstanding') }
  })
} finally {
  await browser.close()
}

if (save().failed) process.exitCode = 1
