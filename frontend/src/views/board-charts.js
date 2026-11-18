// 经营看板 / 大屏共用的图表配置（dark=true 用于大屏深藏青底，不使用发光/霓虹）
import { PALETTE, baseGrid, wanAxis, wanTooltip } from '@/utils/chart'

const C = (dark) => ({
  text: dark ? '#c9d1e6' : '#7b7e86',
  line: dark ? 'rgba(255,255,255,0.12)' : '#efece5',
  main: dark ? '#e8ecf6' : '#1B3275',
  accent: '#B0894F',
})
const axis = (dark) => ({
  axisLabel: { color: C(dark).text }, axisLine: { lineStyle: { color: C(dark).line } },
  splitLine: { lineStyle: { color: C(dark).line } },
})

export function lineBarOption(pay, dark = false) {
  const c = C(dark)
  const rows = pay?.by_line || []
  return {
    grid: baseGrid({ top: 28 }),
    legend: { top: 0, right: 0, textStyle: { color: c.text } },
    tooltip: { trigger: 'axis', valueFormatter: wanTooltip },
    xAxis: { type: 'category', data: rows.map((x) => x.name), ...axis(dark) },
    yAxis: { type: 'value', ...wanAxis, ...axis(dark), axisLabel: { ...wanAxis.axisLabel, color: c.text } },
    series: [
      { name: '当月回款', type: 'bar', data: rows.map((x) => x.amount == null ? null : Number(x.amount)), itemStyle: { color: c.main },
        label: { show: true, position: 'top', color: c.text, fontSize: 11,
                 formatter: (p) => (rows[p.dataIndex].rate != null ? (rows[p.dataIndex].rate * 100).toFixed(0) + '%' : '') } },
      { name: '目标', type: 'bar', data: rows.map((x) => x.target == null ? null : Number(x.target)), itemStyle: { color: dark ? 'rgba(255,255,255,0.22)' : '#e6e2d9' } },
    ],
  }
}

export function dailyOption(pay, dark = false) {
  const c = C(dark)
  const d = pay?.daily || { days: [], actual: [], target: [] }
  return {
    grid: baseGrid({ top: 28 }),
    legend: { top: 0, right: 0, textStyle: { color: c.text } },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : wanTooltip(v)) },
    xAxis: { type: 'category', data: d.days, boundaryGap: false, ...axis(dark) },
    yAxis: { type: 'value', ...axis(dark), axisLabel: { ...wanAxis.axisLabel, color: c.text } },
    series: [
      { name: '累计回款', type: 'line', data: d.actual.map((v) => (v == null ? null : Number(v))), showSymbol: false,
        lineStyle: { width: 2.5, color: c.main }, itemStyle: { color: c.main },
        areaStyle: { color: dark ? 'rgba(232,236,246,0.08)' : 'rgba(27,50,117,0.06)' } },
      { name: '目标线', type: 'line', data: d.target.map(v => v == null ? null : Number(v)), showSymbol: false,
        lineStyle: { type: 'dashed', width: 1.5, color: c.accent }, itemStyle: { color: c.accent } },
    ],
  }
}

export function funnelOption(opp, dark = false) {
  const items = (opp?.funnel || []).filter((x) => x.stage !== 'lost')
  return {
    tooltip: { trigger: 'item', formatter: (p) => `${p.name}<br/>${p.value} 个 · ${p.data.amount == null ? '原表未提供金额' : '¥' + (p.data.amount / 1e4).toFixed(0) + ' 万'}` },
    series: [{
      type: 'funnel', left: '6%', width: '88%', top: 6, bottom: 6, sort: 'none', gap: 2, minSize: '20%',
      label: { color: '#fff', position: 'inside', formatter: '{b}  {c}' },
      itemStyle: { borderColor: dark ? '#14234f' : '#fff' },
      data: items.map((x, i) => ({ name: x.name, value: x.count, amount: x.amount == null ? null : Number(x.amount), drill: x.drill,
                                   itemStyle: { color: dark ? ['#e8ecf6', '#c9d1e6', '#9fadd0', '#7889b8', '#B0894F'][i] : PALETTE[i] },
                                   label: { color: dark && i < 2 ? '#14234f' : '#fff' } })),
    }],
  }
}

export function submitOption(sub, dark = false) {
  const c = C(dark)
  const rows = [...(sub?.weekly?.by_dept || [])].reverse()
  return {
    grid: baseGrid({ top: 6, right: 40 }),
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
               formatter: (p) => { const r = rows[p[0].dataIndex]; return `${r.dept_name}<br/>${r.submitted}/${r.required} 人` } },
    xAxis: { type: 'value', max: 1, ...axis(dark), axisLabel: { color: c.text, formatter: (v) => v * 100 + '%' } },
    yAxis: { type: 'category', data: rows.map((x) => x.dept_name), ...axis(dark) },
    series: [{
      type: 'bar', barMaxWidth: 14, data: rows.map((x) => ({ value: x.rate ?? 0,
        itemStyle: { color: (x.rate ?? 0) < 0.6 ? '#a8741a' : c.main, borderRadius: [0, 3, 3, 0] } })),
      label: { show: true, position: 'right', color: c.text, formatter: (p) => (p.value * 100).toFixed(0) + '%' },
    }],
  }
}

export function monthlyOption(data, dark = false) {
  const c = C(dark)
  const rows = data?.months || []
  const counts = rows.map((x) => ({ sign: x.sign_count, pay: x.pay_count }))
  return {
    grid: baseGrid({ top: 30, bottom: 6 }),
    legend: { top: 0, right: 0, textStyle: { color: c.text } },
    tooltip: {
      trigger: 'axis',
      formatter: (ps) => {
        const i = ps[0]?.dataIndex ?? 0
        const r = rows[i] || {}
        const line = (v) => v == null ? '未提供' : (Number(v) / 1e4).toFixed(1) + ' 万'
        return `${r.month}<br/>新签：${line(r.sign_amount)}（${counts[i]?.sign ?? '—'} 份）<br/>回款：${line(r.pay_amount)}（${counts[i]?.pay ?? '—'} 笔）`
      },
    },
    xAxis: { type: 'category', data: rows.map((x) => `${Number(x.month.slice(5))}月`), ...axis(dark) },
    yAxis: { type: 'value', ...wanAxis, ...axis(dark), axisLabel: { ...wanAxis.axisLabel, color: c.text } },
    series: [
      { name: '新签合同', type: 'bar', barMaxWidth: 18, data: rows.map((x) => x.sign_amount == null ? null : Number(x.sign_amount)),
        itemStyle: { color: c.main },
        label: { show: true, position: 'top', color: c.text, fontSize: 10,
                 formatter: (p) => (rows[p.dataIndex]?.sign_count ? `${rows[p.dataIndex].sign_count}份` : '') } },
      { name: '回款', type: 'bar', barMaxWidth: 18, data: rows.map((x) => x.pay_amount == null ? null : Number(x.pay_amount)),
        itemStyle: { color: c.accent } },
    ],
  }
}
