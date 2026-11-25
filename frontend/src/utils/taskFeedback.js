const beijingTime = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short', hourCycle: 'h23',
})

export const feedbackTime = value => value ? beijingTime.format(new Date(value)) : ''

export const TASK_FILTERS = { ongoing: '进行中', done: '已完成', pending: '待反馈', overdue: '逾期未反馈' }

export function groupTaskFeedback(rows, isDone) {
  const groups = { ongoing: [], done: [], pending: [], overdue: [] }
  for (const task of rows) {
    if (isDone(task)) groups.done.push(task)
    else {
      groups.ongoing.push(task)
      if (task.feedback_status === 'pending') groups.pending.push(task)
      if (task.feedback_status === 'overdue') groups.overdue.push(task)
    }
  }
  return groups
}
