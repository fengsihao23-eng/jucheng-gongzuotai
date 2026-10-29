// Full Pi + real model + isolated API/database + actual Docker workspace.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'
const API = process.env.API || 'http://127.0.0.1:18100'
const WEB = process.env.WEB || 'http://127.0.0.1:15180'
const OUT = process.env.OUT || '/tmp/jc-full-pi-evidence'
assert.equal(new URL(API).port, '18100', 'Use the isolated test API')
fs.mkdirSync(OUT, { recursive: true })
const terminal = new Set(['succeeded','partial','failed','cancelled','waiting_input','waiting_admin','awaiting_confirmation'])
let checks = 0
const check = (label, value=true) => { assert.ok(value,label); checks++; console.log('PASS',label) }
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms))
async function login(name) {
  const response=await fetch(API+'/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:name,password:process.env.DEMO_PASSWORD || 'Jc@2026'})}).then(r=>r.json())
  assert.equal(response.code,0,response.message)
  return {Authorization:'Bearer '+response.data.token,'Content-Type':'application/json'}
}
async function request(url,headers,body) {
  const response=await fetch(API+'/api/v1'+url,{headers,...(body?{method:'POST',body:JSON.stringify(body)}:{})}).then(r=>r.json())
  assert.equal(response.code,0,response.message);return response.data
}
const browser=await chromium.launch({channel:'chrome',headless:true})
const errors=[]
try {
  const headers=await login('admin')
  const existing=await request('/assistant/sessions',headers)
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',timezoneId:'Asia/Shanghai'})
  await context.addInitScript(token=>localStorage.setItem('jc_token',token),headers.Authorization.slice(7))
  const page=await context.newPage()
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(WEB)
  await page.getByRole('button',{name:'小聚',exact:true}).click()
  await page.locator('.xj .ready').waitFor()  // 独立页面：执行引擎就绪标记
  if(existing.length) await page.waitForFunction(id=>document.querySelector('select[aria-label="历史对话"]')?.value===id,existing[0].id)
  await page.getByRole('button',{name:'新对话',exact:true}).click()
  await page.getByLabel('思考深度').selectOption('off')
  async function finish(id) {
    let task
    for(let i=0;i<900;i++) {
      task=await request('/assistant/tasks/'+id,headers)
      if(terminal.has(task.state)) break
      await pause(500)
    }
    assert(terminal.has(task.state),'Task completes within its budget')
    fs.writeFileSync(path.join(OUT,id+'.json'),JSON.stringify(task,null,2))
    console.log('TASK',task.state,task.steps.length,task.message.slice(0,180))
    await page.waitForFunction(([id,state])=>{const card=document.querySelector('.execution-card');return card?.dataset.taskId===id && card?.dataset.taskState===state},[id,task.state],{timeout:20000})
    return task
  }
  async function ask(message) {
    const pending=page.waitForResponse(r=>r.url().endsWith('/assistant/tasks')&&r.request().method()==='POST')
    await page.getByRole('textbox',{name:'向小聚描述需求'}).fill(message)
    await page.getByRole('button',{name:'发送需求',exact:true}).click()
    const response=await(await pending).json();assert.equal(response.code,0,response.message)
    return finish(response.data.id)
  }
  const first=await ask('请使用独立工作区的 Python 和 openpyxl，生成文件 /workspace/小聚验收.xlsx。工作表命名“数据”，第一行为姓名、数量，两条数据分别为张三3、李四5。请实际生成后读取核对，只处理这份测试文件，无需调用公司业务接口。')
  check('实际使用完整 Pi 执行核心',first.engine==='pi-coding-agent'&&first.runtime?.engine==='pi-coding-agent')
  check('Skills 和全部原生工具已启用',first.runtime.skills.some(s=>s.name==='work-files')&&['read','write','edit','bash','find','grep','ls','codemode','tool_search'].every(t=>first.runtime.tools.includes(t)))
  check('文件通过独立工作区实际生成',first.state==='succeeded'&&first.workspace_files.some(f=>f.path==='小聚验收.xlsx'))
  const sid=first.session_id
  const downloaded=await fetch(API+`/api/v1/assistant/sessions/${sid}/files?path=`+encodeURIComponent('小聚验收.xlsx'),{headers})
  const bytes=Buffer.from(await downloaded.arrayBuffer())
  check('结果文件可按本人身份下载',downloaded.ok&&bytes.subarray(0,2).toString()==='PK')
  fs.writeFileSync(path.join(OUT,'小聚验收.xlsx'),bytes)
  const second=await ask('请必须调用 bash，用 openpyxl 重新打开刚才生成的文件，读取后计算两个人的数量合计。不要直接复用上次文字，最后只回答合计数。')
  check('后续请求恢复完整会话和工作文件',second.session_id===sid&&second.steps.some(s=>s.tool==='workspace_tool')&&second.message.includes('8'))
  await page.reload()
  await page.getByRole('button',{name:'小聚',exact:true}).click()
  await page.waitForFunction(sid=>document.querySelector('select[aria-label="历史对话"]')?.value===sid,sid)
  await page.getByText('请必须调用 bash，用 openpyxl 重新打开刚才生成的文件，读取后计算两个人的数量合计。不要直接复用上次文字，最后只回答合计数。',{exact:false}).waitFor()
  check('刷新页面后恢复服务端历史对话')
  await page.screenshot({path:path.join(OUT,'desktop.png'),fullPage:true})
  const forkedResponse=page.waitForResponse(r=>r.url().endsWith(`/sessions/${sid}/fork`))
  await page.getByRole('button',{name:'复制对话',exact:true}).click()
  const forked=await(await forkedResponse).json();assert.equal(forked.code,0,forked.message)
  await page.waitForFunction(id=>document.querySelector('select[aria-label="历史对话"]')?.value===id,forked.data.id)
  const files=await request(`/assistant/sessions/${forked.data.id}/files`,headers)
  check('复制对话保留上下文和独立文件副本',forked.data.id!==sid&&files.some(f=>f.path==='小聚验收.xlsx'))
  const staff=await login('zengli')
  const staffSessions=await request('/assistant/sessions',staff)
  check('员工看不到其他账号的对话',!staffSessions.some(s=>s.id===sid))
  const denied=await fetch(API+`/api/v1/assistant/sessions/${sid}/files?path=`+encodeURIComponent('小聚验收.xlsx'),{headers:staff})
  check('其他账号无法下载工作文件',denied.status===404)
  const pending=page.waitForResponse(r=>r.url().endsWith('/assistant/tasks')&&r.request().method()==='POST')
  await page.getByRole('textbox',{name:'向小聚描述需求'}).fill('请实际调用 bash 执行 sleep 30，然后输出完成，不需要其他操作。')
  await page.getByRole('button',{name:'发送需求',exact:true}).click()
  const created=await(await pending).json();assert.equal(created.code,0)
  let active
  for(let i=0;i<120;i++) {
    active=await request('/assistant/tasks/'+created.data.id,headers)
    if(active.steps.some(s=>s.tool==='workspace_tool'&&s.state==='running')) break
    await pause(500)
  }
  assert(active.steps.some(s=>s.tool==='workspace_tool'&&s.state==='running'))
  const stoppedAt=Date.now()
  await page.getByRole('button',{name:'停止执行',exact:true}).click()
  const stopped=await finish(created.data.id)
  check('停止执行会终止实际容器命令',stopped.state==='cancelled'&&Date.now()-stoppedAt<12000)
  await page.setViewportSize({width:390,height:844})
  await page.waitForFunction(()=>{const r=document.querySelector('.assistant-shell').getBoundingClientRect();return r.width<=innerWidth+1&&r.left>=-1})
  await page.evaluate(()=>{document.querySelector('.assistant-scroll').scrollTop=0})
  await page.screenshot({path:path.join(OUT,'mobile.png'),fullPage:true,animations:'disabled'})
  check('手机对话面板没有横向溢出',await page.evaluate(()=>document.querySelector('.assistant-shell').getBoundingClientRect().width<=innerWidth+1))
  check('浏览器没有脚本错误',errors.length===0)
  console.log(`${checks} checks passed`)
} finally {await browser.close()}
