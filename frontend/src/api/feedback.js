import { ElMessage } from 'element-plus'
import api from './client'

export const feedbackFileLimit = 10
export const feedbackFileSize = 50 * 1024 * 1024

export function submitFeedback(url, data, files = []) {
  return submitFeedbackGroups(url, data, [files])
}

export async function submitFeedbackGroups(url, data, groups) {
  const count = groups.reduce((sum, files) => sum + files.length, 0)
  if (!count) return api.post(url, data)
  if (count > feedbackFileLimit) {
    const message = `每次提交最多上传 ${feedbackFileLimit} 个附件`
    ElMessage.warning(message)
    throw new Error(message)
  }
  const body = new FormData()
  body.append('payload', JSON.stringify(data))
  groups.forEach((files, index) => {
    for (const file of files) body.append(index ? `files-${index}` : 'files', file)
  })
  return api.post(url, body, { timeout: 180000 })
}
