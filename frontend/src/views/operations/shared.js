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

export function taskAttention(task) {
  if (task.can_review) return { label: '待我审核', tone: 'warn', rank: 0 }
  if (task.overdue) return { label: '已逾期', tone: 'down', rank: 1 }
  if (taskStatus(task) === 'blocked') return { label: '受阻', tone: 'down', rank: 2 }
  if (task.needs_update) return { label: '待反馈', tone: 'warn', rank: 3 }
  if (taskStatus(task) === 'review') return { label: statusLabel(task), tone: 'warn', rank: 4 }
  if (taskStatus(task) === 'rejected') return { label: '已退回', tone: 'down', rank: 5 }
  return null
}

export function orderTasks(rows) {
  return [...rows].sort((a, b) => (taskAttention(a)?.rank ?? 6) - (taskAttention(b)?.rank ?? 6) ||
    Number(a.status === 'done') - Number(b.status === 'done') ||
    (a.status === 'done' && b.status === 'done'
      ? (b.completed_at || '').localeCompare(a.completed_at || '')
      : Number(b.priority === 'key') - Number(a.priority === 'key') || a.due_date.localeCompare(b.due_date)) || a.id - b.id)
}

export function taskSummary(rows) {
  const approved = rows.filter(task => task.approval_status === 'approved')
  const total = approved.length, done = approved.filter(task => task.status === 'done').length
  return { total, done, pending: total - done, rate: total ? Math.round(done * 100 / total) : null }
}

export { announceBusinessChange as announceChange } from '@/composables/useBusinessRefresh'
