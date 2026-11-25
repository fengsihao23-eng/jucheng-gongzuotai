<template>
  <details v-if="rows?.length" class="delivery-history">
    <summary>企业微信提醒投递情况</summary>
    <p class="jc-small jc-muted">企微已受理表示发送请求成功。请在企业微信「消息」里的「聚成工作台」会话查看。</p>
    <div v-for="notice in rows" :key="notice.id" class="delivery-record">
      <b>{{ notice.recipient }} · {{ notice.status }}</b>
      <p class="jc-small">{{ notice.title }} · {{ notice.sent_at ? datetime(notice.sent_at) : '待发送/已停止' }}</p>
      <p v-if="notice.error" class="jc-small jc-muted">{{ notice.error }}</p>
    </div>
  </details>
</template>

<script setup>
import { datetime } from '@/utils/format'

defineProps({ rows: Array })
</script>

<style scoped>
.delivery-history { margin-top: 14px; padding: 12px; border: 1px solid var(--jc-line); border-radius: 8px; }
.delivery-history summary { cursor: pointer; }
.delivery-history > p { margin: 8px 0; }
.delivery-record { padding: 8px 0; border-top: 1px solid var(--jc-line-2); }
.delivery-record p { margin: 4px 0 0; overflow-wrap: anywhere; }
</style>
