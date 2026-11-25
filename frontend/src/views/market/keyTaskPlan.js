export function taskPlan(project, initial) {
  return {
    title: initial?.title || '', requirements: initial?.requirements || '',
    owner_id: initial?.owner_id ?? project.owner_id ?? null,
    preparation_on: initial?.preparation_on || project.preparation_on || project.start_date || project.today || null,
    due_on: initial?.plan_date || project.due_on || project.plan_end_date || null,
    cadence: initial?.cadence || 'weekly', feedback_due_at: initial?.feedback_due_at ?? null,
    nodes: (initial?.nodes || []).map(node => ({ id: node.id, name: node.name, due_on: node.due_on })),
  }
}

export function taskDateError(task, project) {
  const start = project.preparation_on || project.start_date
  const end = project.due_on || project.plan_end_date
  if (task.preparation_on > task.due_on) return '任务准备日期不能晚于完成日期'
  if ((start && task.preparation_on < start) || (end && task.due_on > end)) return '任务准备和完成日期须在项目计划日期内'
  if (task.nodes.some(node => !node.name.trim() || !node.due_on || node.due_on < task.preparation_on || node.due_on > task.due_on)) {
    return '填写阶段名称与日期，日期须在任务准备和完成日期之间'
  }
  return ''
}
