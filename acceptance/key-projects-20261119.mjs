import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { API, WEB, call, loginPage, shot, step, save } from './lib.mjs'

const health=await fetch(API+'/health').then(r=>r.json())
assert.equal(health.env,'test','Use an isolated local test service')
assert.ok(['localhost','127.0.0.1'].includes(new URL(API).hostname))
const options=await call('admin','GET','/market/key-options')
const users=(await call('admin','GET','/admin/users',{page_size:100})).items
const worker=users.find(u=>u.userid==='dingrui')
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||undefined})
const desktop=await loginPage(browser,'admin'),mobile=await loginPage(browser,'dingrui',{width:390,height:844})
desktop.setDefaultTimeout(12000);mobile.setDefaultTimeout(12000)
const projectName='合成重点项目浏览器验收 '+Date.now()
let projectCode,task
async function dateField(page,label,value){const input=page.getByRole('combobox',{name:label,exact:true});await input.fill(value);await input.press('Enter')}
async function settled(page){await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'||a.effect?.getTiming().iterations===Infinity))}

await step('KEY-01','重点项目看板名称、三项报告操作及新建入口保持清楚',async()=>{
  await desktop.goto(WEB+'/market')
  await desktop.getByRole('heading',{name:'重点项目',exact:true}).first().waitFor()
  await desktop.getByRole('button',{name:'新建重点项目',exact:true}).waitFor()
  await desktop.getByRole('tab',{name:'周报 / 月报',exact:true}).click()
  for(const name of ['生成周报','生成月报','汇总本周周报'])await desktop.getByRole('button',{name,exact:true}).waitFor()
  await desktop.getByRole('tab',{name:'跟进看板',exact:true}).click()
  await shot(desktop,'key-board-desktop')
})

await step('KEY-02','从页面直接新建重点项目并打开独立管理详情',async()=>{
  await desktop.getByRole('button',{name:'新建重点项目',exact:true}).click()
  const dialog=desktop.getByRole('dialog',{name:'新建重点项目',exact:true})
  await dialog.getByRole('textbox',{name:'重点项目名称',exact:true}).fill(projectName)
  await dialog.getByRole('textbox',{name:'重点项目内容',exact:true}).fill('围绕现场核对、资料准备和方案确认完成本次合成交付。')
  await dateField(dialog,'项目完成日期','2026-11-30')
  await dialog.getByRole('button',{name:'创建重点项目',exact:true}).click()
  await desktop.waitForURL(/\/market\/projects\/JC-/)
  projectCode=new URL(desktop.url()).pathname.split('/').pop()
  await desktop.getByRole('heading',{name:projectName,exact:true}).waitFor()
  assert.equal((await call('admin','GET',`/market/projects/${projectCode}`)).tasks.length,0)
})

await step('KEY-03','安排任务可指定负责人、准备完成日期和阶段节点',async()=>{
  await desktop.getByRole('button',{name:'安排任务',exact:true}).click()
  const dialog=desktop.getByRole('dialog',{name:'安排任务',exact:true})
  await dialog.getByRole('textbox',{name:'重点任务名称',exact:true}).fill('现场资料核对与方案确认')
  await dialog.getByRole('textbox',{name:'重点任务要求',exact:true}).fill('先核对现场资料，再提交方案并记录问题。')
  await dialog.getByRole('combobox',{name:'重点任务负责人',exact:true}).click()
  await desktop.getByRole('option',{name:worker.name,exact:true}).click()
  await dateField(dialog,'任务完成日期','2026-11-28')
  await dialog.locator('label.el-radio').filter({hasText:'每日'}).click()
  await dialog.getByRole('button',{name:'增加阶段节点',exact:true}).click()
  await dialog.getByRole('textbox',{name:'阶段名称1',exact:true}).fill('现场资料核对')
  await dateField(dialog,'阶段日期1','2026-11-20')
  await dialog.getByRole('button',{name:'增加阶段节点',exact:true}).click()
  await dialog.getByRole('textbox',{name:'阶段名称2',exact:true}).fill('方案确认')
  await dateField(dialog,'阶段日期2','2026-11-28')
  await dialog.getByRole('button',{name:'保存安排',exact:true}).click()
  await dialog.waitFor({state:'hidden'})
  await desktop.getByRole('dialog',{name:'任务情况',exact:true}).waitFor()
  task=(await call('admin','GET',`/market/projects/${projectCode}`)).tasks[0]
  assert.equal(task.nodes.length,2);assert.equal(task.cadence,'daily');assert.equal(task.owner_id,worker.id)
  await desktop.getByRole('dialog',{name:'任务情况',exact:true}).getByRole('button',{name:'关闭',exact:true}).click()
})

await step('KEY-04','手机端负责人可填写进展、问题及本次完成节点',async()=>{
  await mobile.goto(`${WEB}/market/projects/${projectCode}?task=${task.id}`)
  const dialog=mobile.getByRole('dialog',{name:'任务情况',exact:true})
  await dialog.getByRole('textbox',{name:'重点任务进展',exact:true}).fill('已核对学校现场资料，确认接口清单。')
  await dialog.getByRole('textbox',{name:'重点任务问题',exact:true}).fill('需要校方补充接口授权。')
  await dialog.getByRole('textbox',{name:'重点任务下一步',exact:true}).fill('收到授权后提交方案确认。')
  await dialog.locator('label.el-checkbox').filter({hasText:'现场资料核对'}).click()
  await dialog.getByRole('button',{name:'提交进展',exact:true}).click()
  await mobile.getByText('进展已保存', {exact:true}).waitFor()
  await dialog.getByRole('button',{name:'过程记录',exact:true}).click()
  const history = mobile.getByRole('dialog',{name:'过程记录',exact:true})
  await history.getByText(/已核对学校现场资料/).first().waitFor()
  await mobile.keyboard.press('Escape')
  await history.waitFor({state:'hidden'})
  const updated=await call('admin','GET',`/market/projects/${projectCode}/tasks/${task.id}`)
  assert.equal(updated.history.length,1);assert.equal(updated.nodes[0].completed_on,'2026-11-19');assert.equal(updated.feedback_due_on,'2026-11-20')
  assert.ok(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))
  assert.equal(await dialog.locator('.el-form-item__error').count(),0)
  await dialog.locator('.el-dialog__body').evaluate(el=>{el.scrollTop=0})
  await settled(mobile);await shot(mobile,'key-task-feedback-mobile')
  await dialog.getByRole('button',{name:'关闭',exact:true}).click()
})

await step('KEY-05','风险反馈联动看板、筛选和原周报汇总',async()=>{
  await desktop.goto(WEB+'/market?tab=key')
  await desktop.getByRole('textbox',{name:'重点项目搜索',exact:true}).fill(projectName)
  await desktop.locator('label.el-checkbox').filter({hasText:'需要关注'}).click()
  await desktop.getByRole('button',{name:`查看重点项目：${projectName}`,exact:true}).waitFor()
  assert.equal(await desktop.locator('.pcard').count(),1)
  const report=await call('admin','GET','/market/reports/summary',{period:'2026-W47'})
  assert.ok(report.text.includes('需要校方补充接口授权'))
  await shot(desktop,'key-board-filtered-desktop')
})

await step('KEY-06','项目任务完成后保留阶段和过程记录',async()=>{
  await mobile.goto(`${WEB}/market/projects/${projectCode}?task=${task.id}`)
  const dialog=mobile.getByRole('dialog',{name:'任务情况',exact:true})
  await dialog.getByRole('textbox',{name:'重点任务进展',exact:true}).fill('校方已补充授权，方案确认完成。')
  await dialog.getByRole('textbox',{name:'重点任务问题',exact:true}).fill('')
  await dialog.getByRole('textbox',{name:'重点任务下一步',exact:true}).fill('归档交付资料。')
  await dialog.locator('label.el-checkbox').filter({hasText:'方案确认'}).click()
  await dialog.locator('label.el-radio-button').filter({hasText:'已完成'}).click()
  await dialog.getByRole('button',{name:'提交进展',exact:true}).click()
  await mobile.getByText('进展已保存',{exact:true}).waitFor()
  await dialog.getByText('已完成',{exact:true}).first().waitFor()
  await dialog.getByRole('button',{name:'关闭',exact:true}).click()
  await mobile.locator('label.el-radio-button').filter({hasText:'已完成'}).click()
  await mobile.getByRole('button').filter({hasText:'现场资料核对与方案确认'}).waitFor()
  const done=await call('admin','GET',`/market/projects/${projectCode}/tasks/${task.id}`)
  assert.equal(done.status,3);assert.equal(done.history.length,2)
})

await step('KEY-07','项目开票与实际到账按月份读取并列出发票',async()=>{
  const number='KEY-BR-'+Date.now()
  await call('admin','POST','/contracts',{contract_no:number,name:'合成重点项目合同',project_code:projectCode,customer_name:'合成验收客户',amount:'10000.00',sign_date:'2026-11-19',business_line:'XY'})
  await call('admin','POST','/receivables/invoices',{invoice_no:'INV-'+Date.now(),issued_on:'2026-11-19',amount:'1200.00',customer:'合成验收客户',contract_no:number})
  await call('admin','POST','/receivables/receipts',{reference:'RECEIPT-'+Date.now(),received_on:'2026-11-19',amount:'300.00',payer:'合成验收客户',account:'合成验收账户',kind:'contract',contract_no:number})
  await desktop.goto(`${WEB}/market/projects/${projectCode}`)
  await desktop.getByRole('tab',{name:'开票回款',exact:true}).click()
  await desktop.getByRole('heading',{name:'开票清单',exact:true}).waitFor()
  await desktop.getByRole('cell',{name:number,exact:true}).first().waitFor()
  const money=(await call('admin','GET',`/market/projects/${projectCode}`)).financials
  assert.equal(money.invoiced,'1200.00');assert.equal(money.received,'300.00')
  await shot(desktop,'key-project-invoices-desktop')
})

await step('KEY-08','运营项目只保留页签，圈出的顶部入口已移除',async()=>{
  const op=await call('admin','POST','/operations',{name:'运营页签去重合成验收',owner_id:users.find(u=>u.userid==='admin').id,enabled_sections:['products','reports','device_maintenance','arrears','collection_maintenance','customers','warehouse','scrap']})
  await desktop.goto(`${WEB}/operations/${op.id}`)
  assert.equal(await desktop.locator('.project-shortcuts').count(),0)
  for(const name of ['设备运维','欠费维护','回款维护','客户资料维护','仓库物料','设备报废','周报 / 月报']){
    const tab=desktop.getByRole('tab',{name,exact:true});await tab.waitFor();await tab.click()
    assert.equal(await desktop.getByRole('button',{name,exact:true}).count(),0)
  }
  await desktop.getByRole('tab',{name:'客户资料维护',exact:true}).click()
  await desktop.getByRole('button',{name:'新增客户资料维护',exact:true}).waitFor()
  await shot(desktop,'operations-tabs-only-desktop')
  const small=await loginPage(browser,'admin',{width:390,height:844})
  await small.goto(`${WEB}/operations/${op.id}`)
  const warehouse=small.getByRole('tab',{name:'仓库物料',exact:true});await warehouse.scrollIntoViewIfNeeded();await warehouse.click()
  await small.getByRole('heading',{name:'仓库物料',exact:true}).waitFor()
  assert.ok(await small.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))
  assert.equal(await small.locator('.project-shortcuts').count(),0)
  await shot(small,'operations-tabs-only-mobile')
})

await step('KEY-09','开票回款页面用当月开票当月到账并显示开票清单',async()=>{
  await desktop.goto(WEB+'/receivables')
  await desktop.getByRole('heading',{name:'开票回款',exact:true}).waitFor()
  for(const name of ['当月开票','当月到账'])await desktop.getByText(name,{exact:true}).waitFor()
  await desktop.getByRole('heading',{name:'开票清单',exact:true}).waitFor()
  await shot(desktop,'invoice-labels-desktop')
})

await browser.close()
const results=save();if(results.failed)process.exitCode=1
