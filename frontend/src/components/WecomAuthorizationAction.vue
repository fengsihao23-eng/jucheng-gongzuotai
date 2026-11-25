<template>
  <span v-if="authorizationUrl" class="wecom-authorization-action" :class="{ compact }">
    <el-button
      tag="a"
      :href="authorizationUrl"
      target="_blank"
      rel="noopener noreferrer"
      :size="compact ? 'default' : 'small'"
      :type="urgent ? 'warning' : 'default'"
      :plain="urgent"
    >{{ actionLabel }}</el-button>
    <el-button v-if="showRetry" size="small" :loading="loading" @click="$emit('retry')">授权完成，重新检查</el-button>
    <span v-if="!compact" class="jc-small jc-muted">授权在企业微信官方页面完成，仅机器人创建者可确认。</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  source: { type: Object, default: null },
  compact: Boolean,
  urgent: Boolean,
  showRetry: Boolean,
  loading: Boolean,
  label: { type: String, default: '' },
})
defineEmits(['retry'])

const authorizationUrl = computed(() => {
  const value = props.source?.authorization_url
  if (!value) return ''
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password
      && (!url.port || url.port === '443')
      && ['work.weixin.qq.com', 'open.work.weixin.qq.com'].includes(url.hostname)
      ? value : ''
  } catch { return '' }
})
const actionLabel = computed(() => props.label || (props.compact ? '企业微信授权' : props.source?.authorization_label || '去企业微信重新授权'))
</script>

<style scoped>
.wecom-authorization-action { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; margin-top: 9px; }
.wecom-authorization-action.compact { display: inline-flex; margin-top: 0; }
</style>
