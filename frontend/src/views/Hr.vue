<!-- 人事中心（PRD 7.5）：概览 / 考勤核对 / 到期预警 / 文书中心 / 招聘 / 人力分析 / 员工答疑 -->
<template>
  <div class="jc-page">
    <PageHeader eyebrow="人事部 · 文书自动化 · 考勤核对 · 人力分析" title="人事中心"
      desc="上传企微考勤导出自动核对异常并生成汇总表；合同与试用期到期自动预警；入职资料包、通知、制度一键生成并提示用工风险；月度人力分析 PPT 自动出稿。">
      <el-button @click="qaOpen = true">员工答疑</el-button>
    </PageHeader>
    <el-tabs v-model="tab" class="hr-tabs" @tab-change="onTab">
      <el-tab-pane label="概览" name="overview" lazy><HrOverview @go="(t) => (tab = t)" /></el-tab-pane>
      <el-tab-pane label="考勤核对" name="attendance" lazy><HrAttendance /></el-tab-pane>
      <el-tab-pane label="到期预警" name="alerts" lazy><HrAlerts /></el-tab-pane>
      <el-tab-pane label="文书中心" name="docs" lazy><HrDocs /></el-tab-pane>
      <el-tab-pane label="招聘" name="recruit" lazy><HrRecruit /></el-tab-pane>
      <el-tab-pane label="人力分析" name="analysis" lazy><HrAnalysis /></el-tab-pane>
      <el-tab-pane label="员工答疑" name="qa" lazy>
        <Panel title="员工自助答疑" sub="考勤、年假、社保、请假规则；结合员工本人数据回答，并给出制度出处">
          <HrQaDialog inline />
        </Panel>
      </el-tab-pane>
    </el-tabs>
    <HrQaDialog v-model="qaOpen" />
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HrQaDialog from '@/components/HrQaDialog.vue'
import HrOverview from './hr/HrOverview.vue'
import HrAttendance from './hr/HrAttendance.vue'
import HrAlerts from './hr/HrAlerts.vue'
import HrDocs from './hr/HrDocs.vue'
import HrRecruit from './hr/HrRecruit.vue'
import HrAnalysis from './hr/HrAnalysis.vue'

const TABS = ['overview', 'attendance', 'alerts', 'docs', 'recruit', 'analysis', 'qa']
const route = useRoute()
const router = useRouter()
const tab = ref(TABS.includes(route.query.tab) ? route.query.tab : 'overview')
const qaOpen = ref(false)
const onTab = (t) => router.replace({ query: { ...route.query, tab: t } })
watch(() => route.query.tab, (t) => { if (TABS.includes(t)) tab.value = t })
</script>

<style scoped>
.hr-tabs :deep(.el-tabs__header) { margin-bottom: 18px; }
</style>
