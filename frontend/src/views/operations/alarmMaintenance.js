import { money } from '@/utils/format'

const school = { key: 'school_name', label: '学校名称', width: 240, fixed: 'left' }
const area = { key: 'area', label: '区域', width: 72 }
const group = { key: 'group', label: '分组', width: 104 }
const billing = { key: 'billing_count', label: '计费个数', width: 94 }
const central = { key: 'central_school', label: '中心校', width: 208 }
const sequence = { key: 'sequence', label: '序号', width: 76 }

export const alarmColumns = {
  device_maintenance: [
    school, area, group,
    { key: 'installation_status', label: '结果判定', width: 96 },
    { key: 'offline_count', label: '离线数量', width: 94 },
    { key: 'camera_status', label: '摄像头状态', width: 106 },
    { key: 'recorder_status', label: '录像机状态', width: 106 },
    { key: 'alarm_box_status', label: '报警盒状态', width: 106 },
    { key: 'camera_ip', label: '摄像头 IP', width: 140 },
    { key: 'recorder_ip', label: '录像机 IP', width: 140 },
    { key: 'alarm_box_ip', label: '报警盒 IP', width: 140 },
    { key: 'equipment_status', label: '配套', width: 88 },
    billing,
    { key: 'closure_status', label: '撤校情况', width: 96 },
    { key: 'removal_date', label: '拆机日期', width: 118 },
    { key: 'alternative_name', label: '其他名称', width: 220 },
    central, sequence,
  ],
  arrears: [
    school, area, group,
    { key: 'amount', label: '应补缴金额', width: 126, format: 'money' },
    { key: 'arrears_months', label: '应补缴月份', width: 108 },
    billing,
    { key: 'billing_start', label: '计费月份（移动合同次月）', width: 140 },
    { key: 'writeoff_month', label: '销账月份', width: 126, format: 'month' },
    { key: 'monthly_due', label: '每月应交额', width: 118, format: 'money' },
    { key: 'due_months', label: '应缴月份', width: 94 },
    { key: 'writeoff_amount', label: '销账金额', width: 118, format: 'money' },
    { key: 'waived_amount', label: '减免金额', width: 110, format: 'money' },
    { key: 'cumulative_amount', label: '累计金额', width: 118, format: 'money' },
    { key: 'paid_months', label: '已缴月份', width: 94 },
    { key: 'unwritten_paid', label: '补缴未销账', width: 114, format: 'money' },
    central, sequence,
  ],
  collection_maintenance: [
    { key: 'receipt_date', label: '到账日期', width: 118 },
    { key: 'receipt_month', label: '月份', width: 106, format: 'month' },
    { key: 'counterparty', label: '对方', width: 280 },
    { key: 'summary', label: '摘要', width: 145 },
    { key: 'amount', label: '到账金额（借方）', width: 154, format: 'money' },
    { key: 'carrier', label: '所属', width: 82 },
  ],
  customers: [
    { key: 'customer_name', label: '所属单位', width: 205, fixed: 'left' },
    { key: 'contact_name', label: '联系人', width: 94 },
    { key: 'phone', label: '联系电话', width: 128 },
    { key: 'contact_position', label: '职务', width: 155 },
    { key: 'address', label: '地址', width: 230 },
    { key: 'contact_gender', label: '性别', width: 64 },
    { key: 'owner_name', label: '负责人', width: 96 },
    { key: 'due_on', label: '下次处理日期', width: 118 },
    { key: 'display_status', label: '状态', width: 86 },
    { key: 'problems', label: '存在问题', width: 155 },
  ],
}

export function alarmValue(row, key) {
  if (key === 'amount') return row.amount
  if (row.kind === 'customers') return row[key]
  if (row.workbook_row) return row.workbook_row.values[key]
  if (key === 'school_name' || key === 'counterparty') return row.customer_name || row.title
  if (key === 'summary') return row.title
}

export function formatAlarmValue(value, { format }) {
  if (value == null || value === '') return '—'
  if (value === '#N/A') return '无数据'
  if (value === '#VALUE!') return '源表公式错误'
  if (format === 'money') return money(value)
  if (format === 'month') {
    const [year, month] = value.split('-')
    return `${year}年${Number(month)}月`
  }
  return value
}
