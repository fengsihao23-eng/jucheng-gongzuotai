export const TASK_STATUS = { todo: '待开始', doing: '进行中', blocked: '遇到阻碍', done: '已完成' }
export const PROJECT_STATUS = { active: '进行中', paused: '已暂停', completed: '已完成' }
export const STAGES = { unknown: '待核对', contact: '接洽中', proposal: '方案沟通', negotiation: '商务洽谈', won: '已签约', operating: '运营中', lost: '暂不推进' }
export const POTENTIAL = { unknown: '未评估', high: '高', medium: '中', low: '低' }
export const unitPrice = v => v == null ? '未标价' : '¥' + Number(v).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 6})
export const ROLES = { manager: '项目管理员', reviewer: '部门审核人', member: '执行成员', viewer: '只读成员' }
export const TASK_SOURCES = { self: '自行安排', company: '公司安排' }
export const APPROVAL_STATUS = { department_pending: '待部门审核', company_pending: '待公司审核', approved: '已生效', rejected: '已退回' }
export const tone = (status) => ({ done: 'up', blocked: 'down', doing: 'brand', todo: 'info' }[status] || 'info')

export const taskStatus = (task) => task.approval_status === 'approved' ? task.status : task.approval_status === 'rejected' ? 'rejected' : 'review'
export const statusLabel = (task) => task.approval_status === 'approved' ? TASK_STATUS[task.status] : APPROVAL_STATUS[task.approval_status]

export function taskSummary(rows) {
  const counts = { todo: 0, doing: 0, blocked: 0, done: 0, review: 0, rejected: 0, company: 0, self: 0, overdue: 0, needsUpdate: 0, carryover: 0 }
  for (const task of rows) {
    counts[taskStatus(task)]++
    if (task.approval_status !== 'approved') continue
    counts[task.source]++
    if (task.overdue) counts.overdue++
    if (task.needs_update) counts.needsUpdate++
    if (task.carryover) counts.carryover++
  }
  const total = counts.company + counts.self
  return { ...counts, total, pending: total - counts.done, rate: total ? Math.round(counts.done * 100 / total) : null }
}

export { announceBusinessChange as announceChange } from '@/composables/useBusinessRefresh'
