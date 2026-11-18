<template>
  <div style="height:100vh;display:flex;align-items:center;justify-content:center" class="jc-muted">{{ msg }}</div>
</template>

<script>
let handledCode = ''
</script>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '@/store/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const msg = ref('正在通过企业微信登录…')
const rawRedirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
const redirect = /^\/(?!\/)/.test(rawRedirect) ? rawRedirect : '/'

onMounted(async () => {
  const code = String(route.query.code || route.query.auth_code || '').trim()
  if (!code) {
    msg.value = '缺少企业微信授权码，正在返回登录页…'
    setTimeout(() => router.replace({ name: 'login', query: { redirect } }), 1500)
    return
  }
  if (handledCode === code) return
  handledCode = code
  try {
    // 立即剥离地址栏中的一次性 code，防止组件重挂载或刷新时重复消费同一 code 触发 401
    window.history.replaceState(window.history.state, '', `/login/callback?redirect=${encodeURIComponent(redirect)}`)
    await auth.loginByCode(code)
    sessionStorage.removeItem('jc_oauth_at')
    await router.replace(redirect)
  } catch (e) {
    handledCode = ''
    msg.value = (e.message || '企业微信登录失败') + '，3 秒后返回登录页'
    setTimeout(() => router.replace({ name: 'login', query: { redirect } }), 3000)
  }
})
</script>
