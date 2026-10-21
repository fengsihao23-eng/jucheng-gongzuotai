// Pi owns the agent loop. Python owns authorized host tools and credentials.
// JSONL over stdio: API keys arrive on stdin, never argv or tool messages.
import { Agent } from '@earendil-works/pi-agent-core'
import { createModels } from '@earendil-works/pi-ai'
import { deepseekProvider } from '@earendil-works/pi-ai/providers/deepseek'

const send = (value) => process.stdout.write(JSON.stringify(value) + '\n')
const pending = new Map()
let buffer = ''
let started = false
let agent
let sequence = 0
process.stdin.setEncoding('utf8')
process.stdin.on('data', (chunk) => {
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
      else if (frame.type === 'cancel') agent?.abort()
    } catch { fail() }
  }
})
process.stdin.on('end', () => { agent?.abort(); process.exit(0) })
function fail() { send({ type: 'error', message: '小聚的 Pi 运行时暂时无法完成请求，请查看执行步骤后重试。' }); process.exitCode = 1; process.stdin.destroy() }

async function run(init) {
  const models = createModels()
  models.setProvider(deepseekProvider())
  const template = models.getModel('deepseek', 'deepseek-flash')
  const model = { ...template, id: init.model, baseUrl: init.baseUrl.replace(/\/chat\/completions\/?$/, '') }
  const tools = init.tools.map((spec) => ({
    ...spec, label: spec.description.split('。')[0], executionMode: 'sequential',
    execute: async (_toolCallId, args) => {
      const id = String(++sequence)
      const result = await new Promise((resolve) => { pending.set(id, resolve); send({ type: 'tool_call', id, name: spec.name, args }) })
      return { content: [{ type: 'text', text: JSON.stringify(result) }], details: {} }
    },
  }))
  let turns = 0
  agent = new Agent({
    initialState: { model, systemPrompt: init.system, thinkingLevel: 'off', tools },
    toolExecution: 'sequential',
    getApiKey: () => init.apiKey,
    streamFn: (m, context, options) => models.streamSimple(m, context, { ...options, maxTokens: 1800, maxRetries: 1 }),
    finishTurn: () => (++turns >= 10 ? { action: 'end' } : undefined),
  })
  await agent.prompt(init.message)
  const last = [...agent.state.messages].reverse().find((m) => m.role === 'assistant')
  if (!last || ['error', 'aborted'].includes(last.stopReason)) { fail(); return }
  const text = last.content.filter((c) => c.type === 'text').map((c) => c.text).join('\n')
  send({ type: 'done', message: text, turns })
  process.stdin.destroy()
}
