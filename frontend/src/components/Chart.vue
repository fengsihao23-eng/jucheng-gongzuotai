<!-- ECharts 封装：注册 jc 主题、自适应尺寸、点击事件、空数据态 -->
<template>
  <div class="chart-wrap" :style="{ height }">
    <div v-show="!empty" ref="el" class="chart" />
    <div v-if="empty" class="empty jc-muted">{{ emptyText }}</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { registerTheme } from '@/utils/chart'

const props = defineProps({
  option: Object, height: { type: String, default: '280px' },
  empty: Boolean, emptyText: { type: String, default: '暂无数据' },
})
const emit = defineEmits(['click'])
const el = ref()
let chart, ro

function render() {
  if (!chart || !props.option) return
  chart.setOption(props.option, true)
}

onMounted(() => {
  registerTheme(echarts)
  chart = echarts.init(el.value, 'jc', { renderer: 'canvas' })
  chart.on('click', (p) => emit('click', p))
  render()
  ro = new ResizeObserver(() => chart?.resize())
  ro.observe(el.value)
})
watch(() => props.option, () => nextTick(render), { deep: true })
watch(() => props.empty, (v) => !v && nextTick(() => { chart?.resize(); render() }))
onBeforeUnmount(() => { ro?.disconnect(); chart?.dispose() })
defineExpose({ getInstance: () => chart })
</script>

<style scoped>
.chart-wrap { position: relative; width: 100%; }
.chart { width: 100%; height: 100%; }
.empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 13px; }
</style>
