// Only this process executes native file/shell tools. Docker supplies /workspace and no credentials.
import { createCodingTools, createGrepTool, createFindTool, createLsTool } from '@earendil-works/pi-coding-agent'
let input = ''
for await (const chunk of process.stdin) input += chunk
try {
  const request = JSON.parse(input)
  const tools = [...createCodingTools('/workspace'), createGrepTool('/workspace'), createFindTool('/workspace'), createLsTool('/workspace')]
  const tool = tools.find((t) => t.name === request.tool)
  if (!tool) throw new Error('Unknown workspace tool')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 80000)
  const result = await tool.execute('workspace', request.args, controller.signal)
  clearTimeout(timer)
  process.stdout.write(JSON.stringify(result))
} catch (error) {
  process.stdout.write(JSON.stringify({ content: [{ type: 'text', text: String(error.message).slice(0, 3000) }], isError: true }))
}
process.exit(0)
