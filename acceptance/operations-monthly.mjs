import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'
import { API, WEB, OUT, call, loginPage } from './lib.mjs'
assert.equal((await fetch(API+'/health').then(r=>r.json())).env,'test')
const people={}
for(const user of ['wujun','xiekai','yangyue','zhangzhenhua'])people[user]=await call(user,'GET','/auth/me')
const name='月度页面验收-'+Date.now().toString(36)
const project=await call('wujun','POST','/operations',{name,owner_id:people.wujun.id})
await call('wujun','POST',`/operations/${project.id}/members`,{user_id:people.xiekai.id,role:'member'})
await call('wujun','POST',`/operations/${project.id}/members`,{user_id:people.yangyue.id,role:'reviewer'})
const company=await call('wujun','POST',`/operations/${project.id}/tasks`,{title:'公司安排对接厂商',owner_id:people.xiekai.id,due_date:'2026-10-25'})
const browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[]
let staff
try{
  staff=await loginPage(browser,'xiekai')
  staff.on('pageerror',e=>errors.push(e.message))
  await staff.goto(`${WEB}/operations/${project.id}`)
  await staff.getByRole('button',{name:'自行安排',exact:true}).click()
  const form=staff.getByRole('dialog',{name:'提交自行安排任务',exact:true})
  await form.getByLabel('要完成什么',{exact:true}).fill('自主计划完成现场复核')
  await form.getByLabel('完成要求',{exact:true}).fill('获得学校需求确认清单')
  await form.getByLabel('截止日期',{exact:true}).fill('2026-10-25');await form.getByLabel('截止日期',{exact:true}).press('Tab')
  await form.getByLabel('部门审核人',{exact:true}).click();await staff.getByRole('option',{name:people.yangyue.name,exact:true}).click()
  await form.getByLabel('公司审核人',{exact:true}).click();await staff.getByRole('option',{name:people.zhangzhenhua.name,exact:true}).click()
  await form.getByRole('button',{name:'提交审核',exact:true}).click()
  await staff.getByRole('heading',{name:'自主计划完成现场复核',exact:true}).waitFor()
  const items=(await call('wujun','GET',`/operations/${project.id}`)).tasks
  const task=items.find(t=>t.source==='self')
  assert.equal(task.approval_status,'department_pending')
  assert.equal(await staff.getByRole('button',{name:'提交进展',exact:true}).count(),0)
  console.log('PASS 成员页面提交自拟计划，待审核期间不进入执行')
  for(const user of ['yangyue','zhangzhenhua']){
    const reviewer=await loginPage(browser,user);reviewer.on('pageerror',e=>errors.push(e.message))
    await reviewer.goto(`${WEB}/operations/${project.id}?task=${task.id}`)
    await reviewer.getByRole('textbox',{name:'审核意见',exact:true}).fill(user==='yangyue'?'部门同意，要求按期完成':'公司同意纳入月计划')
    await reviewer.getByRole('button',{name:'审核通过',exact:true}).click()
    await reviewer.getByText(user==='yangyue'?'部门同意，要求按期完成':'公司同意纳入月计划',{exact:false}).first().waitFor()
    assert.equal((await call(user,'GET',`/operations/tasks/${task.id}`)).approval_status,user==='yangyue'?'company_pending':'approved')
  }
  console.log('PASS 部门、公司不同人员依次从页面审核')
  await staff.goto(`${WEB}/operations/${project.id}?task=${task.id}`)
  await staff.getByPlaceholder('写几句话即可，例如：已和校方确认需求，明天下午提交方案。').fill('学校需求确认清单已经完成')
  await staff.locator('.el-form-item').filter({has:staff.getByText('当前状态',{exact:true})}).locator('.el-select__wrapper').click()
  await staff.getByRole('option',{name:'已完成',exact:true}).click()
  await staff.getByRole('button',{name:'提交进展',exact:true}).click()
  await staff.getByText('已完成 · 100%',{exact:true}).waitFor()
  const boss=await loginPage(browser,'zhangzhenhua');boss.on('pageerror',e=>errors.push(e.message))
  await boss.goto(`${WEB}/operations/${project.id}`)
  await boss.getByRole('cell',{name:'自主计划完成现场复核',exact:false}).waitFor()
  const m=await call('zhangzhenhua','GET','/operations/monthly',{month:'2026-10',project_id:project.id})
  assert.equal(m.stats.done,1);assert.equal(m.stats.pending,1);assert.equal(m.source_counts.self,1);assert.equal(m.source_counts.company,1)
  await boss.screenshot({path:path.join(OUT,'screens','monthly-table.png'),animations:'disabled'})
  const mobile=await loginPage(browser,'xiekai',{width:390,height:844})
  await mobile.goto(`${WEB}/operations/${project.id}`)
  await mobile.getByRole('tab',{name:'月度任务',exact:true}).waitFor()
  assert.ok(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))
  await mobile.screenshot({path:path.join(OUT,'screens','monthly-mobile.png'),animations:'disabled'})
  assert.deepEqual(errors,[])
  fs.writeFileSync(path.join(OUT,'monthly-result.json'),JSON.stringify({passed:true,project_id:project.id,task_id:task.id,company_id:company.id},null,2))
  console.log('PASS 负责人反馈完成，老板月度表按来源显示，移动端无横向溢出')
}catch(e){if(staff)await staff.screenshot({path:path.join(OUT,'screens','monthly-failure.png'),animations:'disabled'});throw e}finally{await browser.close()}
