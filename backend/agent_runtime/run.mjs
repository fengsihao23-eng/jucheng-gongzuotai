// Full Pi manages sessions. Native tools run in a separate credential-free container.
import { mkdirSync, existsSync, chmodSync, renameSync, realpathSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { lazyStream } from '@earendil-works/pi-ai'
import { createAgentSession, SessionManager, SettingsManager, DefaultResourceLoader, ModelRuntime,
  createCodingTools, createGrepTool, createFindTool, createLsTool,
  createCodemodeExtension, createToolSearchExtension, createMcpExtension } from '@earendil-works/pi-coding-agent'
process.umask(0o077)
const runtimeDir = dirname(fileURLToPath(import.meta.url))
const send = value => process.stdout.write(JSON.stringify(value) + '\n')
const pending = new Map()
let buffer = '', started = false, session, sequence = 0, credential = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', chunk => {
  buffer += chunk
  for (;;) {
    const index = buffer.indexOf('\n')
    if (index < 0) break
    const line = buffer.slice(0, index); buffer = buffer.slice(index + 1)
    if (!line) continue
    try {
      const frame = JSON.parse(line)
      if (!started) { started = true; run(frame).catch(fail) }
      else if (frame.type === 'tool_result') { pending.get(frame.id)?.(frame.result); pending.delete(frame.id) }
      else if (frame.type === 'cancel') session?.abort()
      else if (frame.type === 'steer') session?.steer(frame.message)
    } catch { fail() }
  }
})
process.stdin.on('end', () => { session?.dispose(); process.exit(0) })
function fail(error) {
  if(process.env.XIAOJU_RUNTIME_DEBUG === '1') console.error(String(error?.stack || error).split(credential || '\u0000').join('[redacted]'))
  send({type:'error',message:'小聚本次执行未完成，请查看已保存步骤后继续。'}); session?.dispose(); process.exit(1)
}
function callHost(name, args) {
  const id = String(++sequence)
  return new Promise(resolve => { pending.set(id, resolve); send({type:'tool_call',id,name,args}) })
}
async function run(init) {
  credential = init.apiKey || ''
  mkdirSync(init.sessionDir, {recursive:true,mode:0o700})
  const agentDir = join(init.sessionDir,'config'), contextDir = join(init.sessionDir,'context'), sessionFile = join(init.sessionDir,'session.jsonl')
  mkdirSync(agentDir,{recursive:true,mode:0o700}); mkdirSync(contextDir,{recursive:true,mode:0o700})
  if (init.action === 'fork') {
    const manager = SessionManager.forkFrom(init.sourceFile,contextDir,init.sessionDir)
    renameSync(manager.getSessionFile(),sessionFile)
    send({type:'done',message:'已复制对话上下文。'}); process.exit(0)
  }
  const modelRuntime = await ModelRuntime.create({authPath:join(agentDir,'auth.json'),modelsPath:null,allowModelNetwork:false})
  modelRuntime.registerProvider('deepseek',{baseUrl:init.baseUrl.replace(/\/chat\/completions\/?$/,''),apiKey:init.apiKey})
  const template = modelRuntime.getModel('deepseek',init.model) || modelRuntime.getModel('deepseek','deepseek-flash')
  const model = {...template,id:init.model,baseUrl:init.baseUrl.replace(/\/chat\/completions\/?$/,'')}
  const stream = modelRuntime.streamSimple.bind(modelRuntime)
  modelRuntime.streamSimple = (m,context,options) => lazyStream(m,async()=>{
    const scope=await callHost('check_session_scope',{})
    if(!scope.ok) throw new Error('Session access changed')
    return stream(m,context,{...options,maxTokens:init.maxOutputTokens || 8192,maxRetries:1})
  })
  let waitingForConfirmation = false, turns = 0, budgetExceeded = false
  const tools = init.tools.map(spec => ({...spec,label:spec.description.split('。')[0],executionMode:'sequential',execute:async(_id,args)=>{
    const result = await callHost(spec.name,args)
    if(result.requires_confirmation) waitingForConfirmation = true
    return {content:[{type:'text',text:JSON.stringify(result)}],details:{},terminate:result.requires_confirmation === true}
  }}))
  if(init.workspaceEnabled) for(const spec of [...createCodingTools('/workspace'),createGrepTool('/workspace'),createFindTool('/workspace'),createLsTool('/workspace')]) tools.push({...spec,executionMode:'sequential',execute:async(id,args)=>{
    // The SDK advertises trusted skill paths; allow read-only access to these resources.
    if(spec.name === 'read') {
      try { if(realpathSync(args.path).startsWith(realpathSync(join(runtimeDir,'resources')) + '/')) return spec.execute(id,args) } catch { /* regular workspace read */ }
    }
    return callHost('workspace_tool',{tool:spec.name,args})
  }})
  const policy = pi => {
    pi.on('tool_call',()=>waitingForConfirmation ? {block:true,reason:'请等待本人确认具体执行单。',terminate:true}:undefined)
  }
  const settingsManager = SettingsManager.inMemory({defaultTools:['+codemode','+tool_search'],compaction:{enabled:true,reserveTokens:16384,keepRecentTokens:20000},retry:{enabled:true,maxRetries:1,baseDelayMs:1000},cacheWarming:{enabled:false}})
  // Resource directories are trusted application resources, never the script workspace.
  const resourceLoader = new DefaultResourceLoader({cwd:contextDir,agentDir,settingsManager,noContextFiles:true,noSkills:true,noPromptTemplates:true,noThemes:true,noExtensions:true,
    additionalSkillPaths:[join(runtimeDir,'resources/skills')],additionalPromptTemplatePaths:[join(runtimeDir,'resources/prompts')],additionalExtensionPaths:[join(runtimeDir,'resources/extensions/company-policy.mjs')],
    systemPrompt:init.system+'\n工作文件位于独立环境 /workspace；read、write、edit、bash 等工具只操作此环境。用户明确要求实际运行、重新读取或核验文件时，必须在本次调用工具执行，不能只沿用历史结果。网络与公司业务请调用已授权业务工具。',
    extensionFactories:[policy,createCodemodeExtension({mode:'on'}),createToolSearchExtension(),createMcpExtension()]})
  await resourceLoader.reload()
  if(resourceLoader.getExtensions().errors.length) throw new Error('Extension loading failed')
  const manager = SessionManager.open(sessionFile,init.sessionDir,contextDir)
  if(!existsSync(sessionFile) && init.history?.length) for(const message of init.history) manager.appendMessage(message.role === 'user'
    ? {role:'user',content:message.content,timestamp:Date.now()}
    : {role:'assistant',content:[{type:'text',text:message.content}],api:model.api,provider:model.provider,model:model.id,stopReason:'stop',timestamp:Date.now(),usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}})
  ;({session} = await createAgentSession({cwd:contextDir,agentDir,modelRuntime,model,thinkingLevel:init.thinking || 'medium',noTools:'builtin',tools:[...tools.map(t=>t.name),'codemode','tool_search','mcp__*'],customTools:tools,resourceLoader,sessionManager:manager,settingsManager}))
  session.agent.toolExecution = 'sequential'
  const finishTurn = session.agent.finishTurn
  session.agent.finishTurn = async(turn,signal)=>{
    turns++
    if(waitingForConfirmation || turns >= (init.maxTurns || 80)) {budgetExceeded = !waitingForConfirmation; return {action:'end'}}
    return finishTurn?.(turn,signal)
  }
  session.subscribe(event=>{
    if(event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') send({type:'text_delta',delta:event.assistantMessageEvent.delta})
    if(['auto_compaction_start','auto_compaction_end','auto_retry_start','auto_retry_end'].includes(event.type)) send({type:'runtime_event',event:event.type})
  })
  await session.bindExtensions({})
  send({type:'ready',engine:'pi-coding-agent',skills:resourceLoader.getSkills().skills.map(s=>({name:s.name,description:s.description})),tools:session.getActiveToolNames(),thinking:session.thinkingLevel})
  if(init.action === 'compact') {
    let message='已压缩对话上下文，保留摘要和近期执行记录。'
    try {await session.compact()} catch(error) {if(/nothing to compact|not enough messages|already compacted/i.test(error.message)) message='当前上下文较短，暂不需要整理。';else throw error}
    send({type:'done',message,turns})
  }
  else {
    await session.prompt(init.message)
    const last = [...session.messages].reverse().find(m=>m.role === 'assistant')
    if(!last || ['error','aborted'].includes(last.stopReason)) throw new Error('Agent failed')
    send({type:'done',message:session.getLastAssistantText() || (waitingForConfirmation ? '执行单已准备好，等待你确认。':'执行结束。'),turns,budgetExceeded})
  }
  if(existsSync(sessionFile)) chmodSync(sessionFile,0o600)
  session.dispose(); process.exit(0)
}
