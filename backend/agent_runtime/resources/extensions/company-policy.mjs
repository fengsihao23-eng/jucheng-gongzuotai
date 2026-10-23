// Direct SDK bash execution must never run in the API container.
export default function companyPolicy(pi) {
  pi.on('user_bash', () => ({ result: {
    output: '请使用当前对话的 bash 工作区工具执行命令。', exitCode: 1, cancelled: false, truncated: false,
  } }))
}
