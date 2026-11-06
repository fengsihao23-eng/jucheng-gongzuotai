export const TASK_STATUS = { todo: '待开始', doing: '进行中', blocked: '遇到阻碍', done: '已完成' }
export const PROJECT_STATUS = { active: '进行中', paused: '已暂停', completed: '已完成' }
export const STAGES = { contact: '接洽中', proposal: '方案沟通', negotiation: '商务洽谈', won: '已签约', operating: '运营中', lost: '暂不推进' }
export const POTENTIAL = { high: '高', medium: '中', low: '低' }
export const ROLES = { manager: '项目管理员', member: '执行成员', viewer: '只读成员' }
export const tone = (status) => ({ done: 'up', blocked: 'down', doing: 'brand', todo: 'info' }[status] || 'info')

export function announceChange() {
  window.dispatchEvent(new Event('business-data-changed'))
  localStorage.setItem('jc-business-revision', String(Date.now()))
}
