import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '@/api/client'
import { useAuth } from '@/store/auth'

// meta.menu：访问所需菜单权限键（与后端一致）；无 meta.menu 的页面登录即可访问
const pages = [
  { path: '', name: 'portal', component: () => import('@/views/Portal.vue'), meta: { menu: 'portal', title: '工作台' } },
  { path: 'board', name: 'board', component: () => import('@/views/Board.vue'), meta: { menu: 'board', workbookView: 'board', title: '经营看板' } },
  { path: 'reports', name: 'reports', component: () => import('@/views/Reports.vue'), meta: { menu: 'reports', title: '报表中心' } },
  { path: 'submissions', name: 'submissions', component: () => import('@/views/Submissions.vue'), meta: { menu: 'submissions', title: '汇报提交' } },
  { path: 'contracts', name: 'contracts', component: () => import('@/views/WorkbookBusiness.vue'), meta: { menu: 'contracts', workbookView: 'contracts', title: '经营业绩' } },
  { path: 'contracts/items/:sheet/:row', name: 'performance-contract-detail', component: () => import('@/views/PerformanceContractDetail.vue'), meta: { menu: 'contracts', workbookView: 'contracts', title: '项目详情' } },
  { path: 'contracts/:id', name: 'contract-detail', component: () => import('@/views/ContractDetail.vue'), meta: { menu: 'contracts', title: '合同详情' } },
  { path: 'collections', name: 'collections', component: () => import('@/views/CollectionWorkbook.vue'), meta: { anyMenu: ['contracts', 'receivables'], workbookView: 'collections', title: '回款情况' } },
  { path: 'collections/sporadic', name: 'sporadic-collections', component: () => import('@/views/SporadicCollections.vue'), meta: { anyMenu: ['contracts', 'receivables'], workbookView: 'collections', title: '零星回款' } },
  { path: 'collections/items/:sheet/:row', name: 'collection-contract-detail', component: () => import('@/views/PerformanceContractDetail.vue'), meta: { anyMenu: ['contracts', 'receivables'], workbookView: 'collections', title: '项目详情' } },
  { path: 'collections/items/:sheet/:row/report', name: 'collection-project-report', component: () => import('@/views/CollectionProjectReport.vue'), meta: { anyMenu: ['contracts', 'receivables'], workbookView: 'collections', title: '项目回款报告' } },
  { path: 'collections/items/:sheet/:row/execution', name: 'collection-execution', component: () => import('@/views/CollectionExecution.vue'), meta: { anyMenu: ['contracts', 'receivables'], workbookView: 'collections', title: '回款执行情况' } },
  { path: 'collections/followups/:id', name: 'collection-followup', component: () => import('@/views/CollectionFollowupDetail.vue'), meta: { feedbackOverlay: true, workbookView: 'collections', title: '回款跟进' } },
  { path: 'approvals', name: 'approvals', component: () => import('@/views/Approvals.vue'), meta: { menu: 'contracts', title: '审批台账' } },
  { path: 'opportunities', name: 'opportunities', component: () => import('@/views/OpportunityWorkbook.vue'), meta: { menu: 'opportunities', workbookView: 'opportunities', title: '商机信息' } },
  { path: 'performance', name: 'performance', component: () => import('@/views/Performance.vue'), meta: { menu: 'performance', title: '业绩与运营' } },
  { path: 'clean', name: 'clean', component: () => import('@/views/Clean.vue'), meta: { menu: 'clean', title: '数据清洗' } },
  { path: 'expenses', name: 'expenses', component: () => import('@/views/Expenses.vue'), meta: { menu: 'expenses', title: '报销归集' } },
  { path: 'flows', name: 'flows', component: () => import('@/views/Flows.vue'), meta: { menu: 'flows', title: '流水核对' } },
  { path: 'receivables', name: 'receivables', component: () => import('@/views/FinanceLedgerWorkspace.vue'), meta: { menu: 'receivables', title: '开票回款' } },
  { path: 'journal', name: 'journal', component: () => import('@/views/FinanceLedgerWorkspace.vue'), meta: { menu: 'journal', title: '出纳日记账' } },
  { path: 'profit', name: 'profit', component: () => import('@/views/Profit.vue'), meta: { menu: 'profit', title: '项目利润' } },
  { path: 'finance-data', name: 'finance-data', component: () => import('@/views/FinanceData.vue'), meta: { menu: 'finance_data', title: '财务数据' } },
  { path: 'market', name: 'market', component: () => import('@/views/MarketFollow.vue'), meta: { menu: 'market', title: '重点项目' } },
  { path: 'market/projects/:code', name: 'key-project-detail', component: () => import('@/views/KeyProjectDetail.vue'), meta: { anyMenu: ['market', 'projects'], title: '重点项目详情' } },
  { path: 'operations', name: 'operations', component: () => import('@/views/Operations.vue'), meta: { menu: 'operations', title: '运营项目' } },
  { path: 'operations/:id', name: 'operation-detail', component: () => import('@/views/OperationDetail.vue'), meta: { menu: 'operations', title: '运营项目跟进' } },
  { path: 'tender-projects', name: 'tender-projects', component: () => import('@/views/TenderWorkbook.vue'), meta: { menu: 'projects', workbookView: 'tender', title: '招标与在建' } },
  { path: 'general-projects', name: 'general-projects', component: () => import('@/views/Projects.vue'), meta: { menu: 'projects', projectMode: 'general', title: '一般项目' } },
  { path: 'history-projects', name: 'history-projects', component: () => import('@/views/Projects.vue'), meta: { menu: 'projects', projectMode: 'history', title: '历史项目' } },
  { path: 'projects', name: 'projects', component: () => import('@/views/Projects.vue'), meta: { menu: 'projects', title: '项目台账' } },
  { path: 'projects/:code', name: 'project-detail', component: () => import('@/views/ProjectDetail.vue'), meta: { menu: 'projects', title: '项目详情' } },
  { path: 'intel', name: 'intel', component: () => import('@/views/Intel.vue'), meta: { menu: 'intel', title: '竞品情报' } },
  { path: 'reviews', name: 'reviews', component: () => import('@/views/Reviews.vue'), meta: { menu: 'reviews', title: '项目复盘' } },
  { path: 'tender', name: 'tender', component: () => import('@/views/Tender.vue'), meta: { menu: 'tender', title: '标书助手' } },
  { path: 'service', name: 'service', component: () => import('@/views/Service.vue'), meta: { menu: 'service', title: '客服工单' } },
  { path: 'hr', name: 'hr', component: () => import('@/views/Hr.vue'), meta: { menu: 'hr', title: '人事中心' } },
  { path: 'kb', name: 'kb', component: () => import('@/views/Kb.vue'), meta: { menu: 'kb', title: '知识库' } },
  { path: 'imports', name: 'imports', component: () => import('@/views/Imports.vue'), meta: { menu: 'imports', title: '资料导入' } },
  { path: 'workbuddy', name: 'mcp', component: () => import('@/views/McpConfig.vue'), meta: { menu: 'mcp', title: 'WorkBuddy 接入' } },
  { path: 'admin', name: 'admin', component: () => import('@/views/Admin.vue'), meta: { menu: 'admin', title: '系统管理' } },
  { path: 'operation-records', name: 'operation-records', component: () => import('@/views/OperationRecords.vue'), meta: { menu: 'operation_records', title: '操作记录', workbookView: 'audit' } },
  { path: 'admin/wecom-api', name: 'wecom-api', component: () => import('@/views/WecomApi.vue'), meta: { menu: 'admin', title: '企业微信接口' } },
  { path: 'notifications', name: 'notifications', component: () => import('@/views/Notifications.vue'), meta: { title: '消息通知' } },
  { path: 'record-followups/:id', name: 'linked-record', component: { render: () => null }, meta: { title: '业务跟进', feedbackOverlay: true } },
  { path: 'account', name: 'account', component: () => import('@/views/Account.vue'), meta: { title: '个人中心' } },
]

const detailLists = new Set(['market', 'general-projects', 'history-projects', 'projects', 'contracts', 'collections', 'opportunities', 'tender-projects'])
const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: (to, from, saved) => {
    if (to.meta.feedbackOverlay || from.meta.feedbackOverlay || (to.name === 'collections' && to.path === from.path)) return saved || false
    return (detailLists.has(to.name) && saved) || { top: 0 }
  },
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/Login.vue'), meta: { public: true, title: '登录' } },
    { path: '/login/callback', name: 'login-callback', component: () => import('@/views/LoginCallback.vue'), meta: { public: true } },
    { path: '/403', name: 'forbidden', component: () => import('@/views/Forbidden.vue'), meta: { title: '无权限' } },
    { path: '/xiaoju/:id?', name: 'xiaoju', component: () => import('@/views/Xiaoju.vue'), meta: { title: '小聚' } },
    { path: '/board/screen', name: 'board-screen', component: () => import('@/views/BoardScreen.vue'), meta: { menu: 'board', workbookView: 'board', title: '经营大屏' } },
    { path: '/', component: () => import('@/layouts/MainLayout.vue'), children: pages },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  document.title = (to.meta.title ? to.meta.title + ' · ' : '') + '聚成数智工作台'
  if (to.meta.public) return true
  if (to.name === 'expenses' && ['bank_expense', 'cash_journal'].includes(to.query.ledger)) {
    return { name: 'journal', query: to.query }
  }
  if (to.name === 'receivables' && to.query.ledger === 'bank_receipt') {
    return { name: 'journal', query: to.query }
  }
  if (to.path === '/' && typeof to.query.xiaoju === 'string') return { name: 'xiaoju', params: { id: to.query.xiaoju } }  // 旧版企业微信回复链接
  if (!getToken()) return { name: 'login', query: { redirect: to.fullPath } }
  const auth = useAuth()
  try { await auth.fetchMe() }
  catch {
    if (!getToken() || !auth.profile) return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (auth.profile?.workbench_enabled === false && to.name !== 'forbidden') return { name: 'forbidden' }
  if (to.meta.adminOnly && !auth.profile?.is_admin) return { name: 'forbidden' }
  if ((to.meta.menu && !auth.hasMenu(to.meta.menu)) || (to.meta.anyMenu && !to.meta.anyMenu.some(k => auth.hasMenu(k)))) return { name: 'forbidden' }
  return true
})

export default router
