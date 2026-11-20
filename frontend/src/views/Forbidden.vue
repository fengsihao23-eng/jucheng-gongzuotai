<template>
  <div class="jc-page" style="text-align:center;padding-top:120px">
    <div class="jc-serif" style="font-size:56px;color:var(--jc-ink-4)">403</div>
    <p class="jc-muted">{{ blocked ? '当前账号无法查看和使用工作台。请联系管理员单独重新配置权限。' : '当前账号没有访问该页面的权限。如需开通，请联系平台管理员配置权限。' }}</p>
    <el-button v-if="!blocked" type="primary" @click="$router.replace('/')">返回工作台</el-button>
    <el-button @click="refresh" :loading="loading">重新检查权限</el-button>
    <el-button @click="logout">退出登录</el-button>
  </div>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '@/store/auth'
const auth = useAuth(), router = useRouter(), loading = ref(false)
const blocked = computed(() => auth.profile?.workbench_enabled === false)
async function refresh() {
  loading.value = true
  try { await auth.fetchMe(); if (!blocked.value) router.replace('/') } finally { loading.value = false }
}
async function logout() { await auth.logout(); router.replace('/login') }
</script>
