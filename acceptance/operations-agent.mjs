// Real model + application runtime. Only synthetic records in an isolated test service.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { API, OUT, call } from './lib.mjs'

assert.equal((await fetch(API+'/health').then(r=>r.json())).env,'test')
assert.equal(new URL(API).hostname,'127.0.0.1')
const runtime=(await call('admin','GET','/assistant/catalog')).runtime
assert.equal(runtime.available,true,'真实模型运行时应已配置')
const terminal=new Set(['succeeded','partial','waiting_input','waiting_admin','awaiting_confirmation','failed','cancelled'])
const marker=Date.now().toString(36), title='月度闭环模型验收-'+marker
const people={}
for(const user of ['wujun','xiekai','yangyue','zhangzhenhua'])people[user]=await call(user,'GET','/auth/me')
const project=await call('wujun','POST','/operations',{name:title,owner_id:people.wujun.id,objective:'核验真实对话派单、审核、反馈和月度汇总'})
await call('wujun','POST',`/operations/${project.id}/members`,{user_id:people.xiekai.id,role:'member'})
await call('wujun','POST',`/operations/${project.id}/members`,{user_id:people.yangyue.id,role:'reviewer'})
async function ask(user,message){
  const created=await call(user,'POST','/assistant/tasks',{message,current_page:`/operations/${project.id}`,thinking:'off'})
  console.log('START',user,created.id)
  let task
  for(let n=0;n<360;n++){
    task=await call(user,'GET','/assistant/tasks/'+created.id)
    if(terminal.has(task.state))break
    await new Promise(r=>setTimeout(r,1000))
  }
  fs.writeFileSync(path.join(OUT,created.id+'.json'),JSON.stringify(task,null,2))
  console.log('RESULT',user,task.state,task.message?.slice(0,220))
  assert.ok(terminal.has(task.state),'模型调用应结束')
  return task
}
async function confirm(user,task,operation){
  assert.equal(task.state,'awaiting_confirmation',JSON.stringify(task.business_results))
  const actions=task.business_actions.filter(a=>a.operation_id===operation)
  assert.equal(actions.length,1,'需要且只需要一个对应执行单')
  const r=await call(user,'POST',`/assistant/business/actions/${actions[0].id}/confirm`,{approve:true})
  assert.equal(r.result.ok,true)
  return r.result.data
}
const staff=people.xiekai.name,dept=people.yangyue.name,boss=people.zhangzhenhua.name
const assignedTitle='公司安排现场确认-'+marker
const assigned=await ask('zhangzhenhua',`先读取运营项目「${title}」的任务情况，再给团队成员${staff}安排「${assignedTitle}」。这是公司安排，纳入2026年10月月度任务，2026年10月25日前完成，要求取得学校需求清单，每天反馈进展。`)
assert.ok((await call('wujun','GET',`/operations/${project.id}`)).tasks.length===0,'执行确认前不能派单')
const company=await confirm('zhangzhenhua',assigned,'operations.create_operation_task')
assert.equal(company.source,'company');assert.equal(company.approval_status,'approved');assert.equal(company.plan_month,'2026-10')
console.log('PASS 老板通过真实智能体读取信息、派发公司任务并自动进入月度表')
const ownTitle='自行安排需求复核-'+marker
const proposed=await ask('xiekai',`在运营项目「${title}」里，为我自己提交自行安排任务「${ownTitle}」，要求复核需求清单，截止2026年10月25日，计划月份2026年10月，按节点反馈。指定项目团队中的${dept}做部门审核，${boss}做公司审核。先提交审核，不要替审核人批准。`)
const own=await confirm('xiekai',proposed,'operations.create_operation_task')
assert.equal(own.source,'self');assert.equal(own.approval_status,'department_pending')
console.log('PASS 本人对话提交自行安排，确认动作没有跳过审批')
const first=await ask('yangyue',`请读取运营项目「${title}」中「${ownTitle}」的最新任务内容，作为指定部门审核人审核通过，意见是“同意需求复核安排，按期完成”。这是部门审核。`)
const deptResult=await confirm('yangyue',first,'operations.review_operation_task')
assert.equal(deptResult.approval_status,'company_pending')
const second=await ask('zhangzhenhua',`请读取运营项目「${title}」中「${ownTitle}」的最新部门审核结果，作为指定公司审核人审核通过，意见是“同意纳入10月计划”。`)
const approved=await confirm('zhangzhenhua',second,'operations.review_operation_task')
assert.equal(approved.approval_status,'approved');assert.equal(approved.reviews.length,2)
console.log('PASS 真实智能体完成部门、公司两级审核且顺序正确')
const feedback=await ask('xiekai',`我在运营项目「${title}」中的任务「${ownTitle}」已经完成：需求清单复核完成，并已发给项目负责人。请将状态更新为已完成，进度100%，提交这个结果。`)
const complete=await confirm('xiekai',feedback,'operations.operation_feedback')
assert.equal(complete.status,'done')
const summary=await ask('zhangzhenhua',`汇总运营项目「${title}」2026年10月的月度任务，分公司安排和自行安排，说明哪些完成了、哪些没完成及原因。只读取实际记录。`)
assert.equal(summary.state,'succeeded')
assert.ok(summary.business_results.some(r=>r.ok && r.operation_id==='operations.monthly_plan'))
const monthly=await call('wujun','GET','/operations/monthly',{month:'2026-10',project_id:project.id})
assert.equal(monthly.stats.total,2);assert.equal(monthly.stats.done,1);assert.equal(monthly.stats.pending,1)
const progress = await ask('zhangzhenhua',`填写运营项目「${title}」的项目整体进度：进度情况是需求清单复核完成，遇到的问题是部分设备报价待补，下一步计划是与供应方核实设备报价，计划完成时间为2026-10-27。`)
const written = await confirm('zhangzhenhua', progress, 'operations.record_project_progress')
assert.equal(written.next_due_date, '2026-10-27'); assert.match(written.next_plan, /报价/)
assert.equal((await call('wujun','GET',`/operations/${project.id}`)).latest_progress.id, written.id)
console.log('PASS 真实模型填写项目整体进度、问题、下一步及计划完成日期')
fs.writeFileSync(path.join(OUT,'operations-agent-result.json'),JSON.stringify({project_id:project.id,company_task:company.id,self_task:own.id,model:runtime.model,passed:true},null,2))
console.log('PASS 实际反馈写入后老板对话汇总月度结果；真实模型闭环验收完成')
