// ECharts 主题：纸面底 + 藏青主色 + 低饱和辅色；细网格线，无阴影无渐变霓虹。
export const PALETTE = ['#1B3275', '#B0894F', '#5D7F73', '#9A5B4A', '#7D8AA6', '#C9B48A', '#3E5A99', '#8E9A7E']
export const INK = { ink: '#1d1f24', ink2: '#4a4d55', ink3: '#7b7e86', line: '#e6e2d9', line2: '#efece5' }

let registered = false
export function registerTheme(echarts) {
  if (registered) return
  registered = true
  const axis = {
    axisLine: { lineStyle: { color: INK.line } },
    axisTick: { show: false },
    axisLabel: { color: INK.ink3, fontSize: 11 },
    splitLine: { lineStyle: { color: INK.line2 } },
  }
  echarts.registerTheme('jc', {
    color: PALETTE,
    backgroundColor: 'transparent',
    textStyle: { fontFamily: 'PingFang SC, Microsoft YaHei, sans-serif', color: INK.ink2 },
    title: { textStyle: { color: INK.ink, fontSize: 14, fontWeight: 600 } },
    legend: { textStyle: { color: INK.ink3, fontSize: 12 }, icon: 'roundRect', itemWidth: 10, itemHeight: 10 },
    tooltip: {
      backgroundColor: '#fff', borderColor: INK.line, borderWidth: 1, textStyle: { color: INK.ink, fontSize: 12 },
      extraCssText: 'box-shadow:0 6px 20px rgba(29,31,36,.10);border-radius:8px;',
    },
    categoryAxis: axis, valueAxis: { ...axis, axisLine: { show: false } }, timeAxis: axis, logAxis: axis,
    line: { symbol: 'circle', symbolSize: 5, smooth: false, lineStyle: { width: 2 } },
    bar: { barMaxWidth: 26, itemStyle: { borderRadius: [3, 3, 0, 0] } },
    pie: { itemStyle: { borderColor: '#fff', borderWidth: 2 } },
    funnel: { itemStyle: { borderColor: '#fff', borderWidth: 1 } },
  })
}

export const baseGrid = (extra = {}) => ({ left: 8, right: 16, top: 32, bottom: 8, containLabel: true, ...extra })
export const wanAxis = { axisLabel: { formatter: (v) => (Math.abs(v) >= 1e4 ? v / 1e4 + '万' : v) } }
export const wanTooltip = (v) => '¥' + Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
