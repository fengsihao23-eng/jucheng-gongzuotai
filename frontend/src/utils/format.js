// 金额/数字/日期格式化。后端金额为字符串（单位元），这里统一转换展示。
import dayjs from 'dayjs'

export const num = (v) => (v === null || v === undefined || v === '' ? 0 : Number(v))

/** ¥1,234,567.00 */
export function money(v, digits = 2) {
  if (v === null || v === undefined || v === '') return '—'
  return '¥' + num(v).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** 486.0 万 / 1.23 亿；small 时直接显示元 */
export function wan(v, digits = 1) {
  const n = num(v)
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿'
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(digits) + ' 万'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 元'
}

/** 只返回数值部分（万），用于 KPI 大数字 */
export const wanNum = (v, digits = 1) => (num(v) / 1e4).toFixed(digits)

export function percent(v, digits = 1) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—'
  return (num(v) * 100).toFixed(digits) + '%'
}

export const thousands = (v) => num(v).toLocaleString('zh-CN')

export const date = (v) => (v ? dayjs(v).format('YYYY-MM-DD') : '—')
export const datetime = (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '—')
export const shortDate = (v) => (v ? dayjs(v).format('MM-DD') : '—')
export const time = (v) => (v ? dayjs(v).format('HH:mm') : '—')

export function fromNow(v) {
  if (!v) return '—'
  const m = dayjs().diff(dayjs(v), 'minute')
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  if (m < 1440) return `${Math.floor(m / 60)} 小时前`
  if (m < 1440 * 7) return `${Math.floor(m / 1440)} 天前`
  return date(v)
}

export function greeting() {
  const h = new Date().getHours()
  return h < 6 ? '夜深了' : h < 11 ? '早上好' : h < 13 ? '中午好' : h < 18 ? '下午好' : '晚上好'
}

export const filesize = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : (b / 1024).toFixed(0) + ' KB')

export const BUSINESS_LINES = { XY: '数字校园', AF: '综合智慧安防', KP: '素质教育考评', YW: '运维服务', TD: '待定' }
export const PROJECT_STATUS = { 1: '招标中', 2: '在建', 3: '已验收', 4: '运维', 5: '结项' }
export const MILESTONE_STATUS = { 1: '未开始', 2: '进行中', 3: '已完成', 4: '延期' }
export const CONTRACT_STATUS = { 1: '履行中', 2: '已归档', 3: '已终止' }
export const EXPENSE_STATUS = { 1: '审批中', 2: '待归集', 3: '已驳回', 4: '已归集' }
export const FLOW_MATCH = { 0: '未核对', 1: '已关联报销', 2: '已关联回款', 3: '公户支出', 4: '已关联项目成本', 5: '其他收入' }
export const OPP_STAGES = { lead: '线索', contact: '初步接触', quote: '方案报价', negotiation: '商务谈判', won: '赢单', lost: '丢单' }
