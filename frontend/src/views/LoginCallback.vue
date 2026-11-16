<template>
  <div style="height:100vh;display:flex;align-items:center;justify-content:center" class="jc-muted">{{ msg }}</div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '@/store/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const msg = ref('正在通过企业微信登录…')
const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
onMounted(async () => {
  try {
    await auth.loginByCode(route.query.code || route.query.auth_code)
    router.replace(redirect)
  } catch (e) {
    msg.value = e.message + '，3 秒后返回登录页'
    setTimeout(() => router.replace({ name: 'login', query: { redirect } }), 3000)
  }
})
</script>
