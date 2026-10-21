// 侧边菜单（分组）。key 与后端 app/core/permissions.py MENUS 一致，按用户 menus 过滤。
export const MENU_GROUPS = [
  { title: '', items: [{ key: 'portal', path: '/', label: '工作台' }] },
  {
    title: '经营',
    items: [
      { key: 'board', path: '/board', label: '经营看板' },
      { key: 'reports', path: '/reports', label: '报表中心' },
      { key: 'submissions', path: '/submissions', label: '汇报提交' },
    ],
  },
  {
    title: '台账',
    items: [
      { key: 'contracts', path: '/contracts', label: '合同台账' },
      { key: 'opportunities', path: '/opportunities', label: '商机信息' },
      { key: 'performance', path: '/performance', label: '业绩与运营' },
      { key: 'clean', path: '/clean', label: '数据清洗' },
    ],
  },
  {
    title: '财务',
    items: [
      { key: 'expenses', path: '/expenses', label: '报销归集' },
      { key: 'flows', path: '/flows', label: '流水核对' },
      { key: 'journal', path: '/journal', label: '出纳日记账' },
      { key: 'profit', path: '/profit', label: '项目利润' },
      { key: 'finance_data', path: '/finance-data', label: '财务数据' },
    ],
  },
  {
    title: '市场与项目',
    items: [
      { key: 'market', path: '/market', label: '重点项目跟进' },
      { key: 'projects', path: '/projects', label: '项目流程' },
      { key: 'intel', path: '/intel', label: '竞品情报' },
      { key: 'reviews', path: '/reviews', label: '项目复盘' },
    ],
  },
  {
    title: '部门场景',
    items: [
      { key: 'tender', path: '/tender', label: '标书助手' },
      { key: 'service', path: '/service', label: '客服工单' },
      { key: 'hr', path: '/hr', label: '人事中心' },
    ],
  },
  {
    title: '公共',
    items: [
      { key: 'kb', path: '/kb', label: '知识库' },
      { key: 'mcp', path: '/workbuddy', label: 'WorkBuddy 接入' },
      { key: 'admin', path: '/admin', label: '系统管理' },
    ],
  },
]
