<template>
  <div class="login">
    <section class="left">
      <JcLogo :size="40" inverse />
      <div class="pitch">
        <div class="eyebrow">UNIFIED WORKSPACE</div>
        <h1 class="jc-serif">一次录入，<br />全程流转。</h1>
        <p>企业微信审批、银行流水、柠檬云账套与各部门台账在这里归集，<br />报表与看板自动生成，WorkBuddy 一句话即可调用。</p>
      </div>
      <ul class="facts">
        <li><b class="jc-num">3</b><span>大核心业务<br />数字校园 · 智慧安防 · 素质教育考评</span></li>
        <li><b class="jc-num">8</b><span>个部门场景<br />总办 · 财务 · 市场优先上线</span></li>
        <li><b class="jc-num">11</b><span>个 MCP 工具<br />接入 WorkBuddy</span></li>
      </ul>
      <div class="foot">广东聚成数智科技有限公司 · 国家高新技术企业</div>
    </section>

    <section class="right">
      <div class="box">
        <h2 class="jc-serif">登录工作台</h2>
        <p class="jc-muted">使用企业微信账号或姓名、手机号登录</p>

        <el-form v-if="cfg.password_login" class="mt-24" :model="form" @submit.prevent="submit">
          <el-form-item>
            <el-input v-model="form.username" size="large" placeholder="企业微信账号 / 姓名 / 手机号" autocomplete="username" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="form.password" size="large" type="password" show-password placeholder="密码" autocomplete="current-password" @keyup.enter="submit" />
          </el-form-item>
          <div class="jc-row between mb-16">
            <el-checkbox v-model="remember">记住账号</el-checkbox>
            <el-tooltip content="请联系平台管理员重置密码" placement="top"><span class="jc-muted jc-small link">忘记密码</span></el-tooltip>
          </div>
          <el-button type="primary" size="large" class="full" :loading="loading" native-type="submit">登录</el-button>
        </el-form>

        <div class="or"><span>或</span></div>
        <el-button size="large" class="full" @click="wecom(false)">{{ inWeCom ? '使用企业微信账号登录' : '企业微信扫码登录' }}</el-button>

        <div v-if="cfg.demo_accounts?.length" class="demo">
          <div class="jc-row between">
            <span class="jc-eyebrow">演示账号 · 仅演示环境可见</span>
            <span class="jc-muted jc-small">统一口令 {{ cfg.demo_password_hint }}</span>
          </div>
          <el-select v-model="demo" filterable placeholder="按姓名、部门或岗位搜索" class="mt-8" style="width: 100%" @change="pickDemo">
            <el-option-group v-for="(list, dept) in demoGroups" :key="dept" :label="dept">
              <el-option v-for="a in list" :key="a.userid" :value="a.userid" :label="`${a.name} · ${a.position}`" />
            </el-option-group>
          </el-select>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api/client'
import { useAuth } from '@/store/auth'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const cfg = ref({ password_login: true, demo_accounts: [] })
const form = reactive({ username: localStorage.getItem('jc_last_user') || '', password: '' })
const remember = ref(!!localStorage.getItem('jc_last_user'))
const loading = ref(false)
const demo = ref('')

const inWeCom = /wxwork/i.test(navigator.userAgent)
const demoGroups = computed(() => {
  const g = {}
  cfg.value.demo_accounts.forEach((a) => (g[a.dept] ||= []).push(a))
  return g
})

async function submit() {
  if (!form.username || !form.password) return ElMessage.warning('请输入账号和密码')
  loading.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    remember.value ? localStorage.setItem('jc_last_user', form.username.trim()) : localStorage.removeItem('jc_last_user')
    router.replace(route.query.redirect || '/')
  } finally {
    loading.value = false
  }
}
function pickDemo(uid) {
  form.username = uid
  form.password = cfg.value.demo_password_hint
  submit()
}
async function wecom(auto = false) {
  const r = await api.get('/auth/wecom/authorize-url', { qr: !inWeCom, redirect: route.query.redirect || '/' })
  if (r.mock) {
    if (!auto) ElMessage.info('当前为演示数据模式，企业微信凭证配置后即可扫码登录')
    return
  }
  location.href = r.url
}
onMounted(async () => {
  cfg.value = await api.get('/auth/login-config')
  if (!inWeCom || cfg.value.wecom_mode !== 'live' || !cfg.value.corp_id || !cfg.value.agent_id) return
  // 企微内打开应用即静默授权；30 秒内失败过一次就不再自动跳转，避免死循环
  const last = Number(sessionStorage.getItem('jc_oauth_at') || 0)
  if (Date.now() - last < 30000) return
  sessionStorage.setItem('jc_oauth_at', String(Date.now()))
  await wecom(true)
})
</script>

<style scoped>
.login { min-height: 100vh; display: grid; grid-template-columns: minmax(420px, 1.1fr) minmax(420px, 1fr); }
.left {
  background: var(--jc-brand); color: #fff; padding: 48px 56px; display: flex; flex-direction: column;
  background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
  background-size: 48px 48px;
}
.pitch { margin-top: auto; }
.eyebrow { font-size: 12px; letter-spacing: 0.3em; color: rgba(255,255,255,.55); }
.pitch h1 { font-size: 48px; line-height: 1.25; margin: 16px 0 20px; font-weight: 600; }
.pitch p { color: rgba(255,255,255,.72); line-height: 1.9; margin: 0; font-size: 15px; }
.facts { list-style: none; padding: 0; margin: 48px 0 auto; display: flex; gap: 40px; border-top: 1px solid rgba(255,255,255,.16); padding-top: 24px; }
.facts li { display: flex; gap: 12px; align-items: flex-start; }
.facts b { font-size: 34px; font-weight: 600; line-height: 1; }
.facts span { font-size: 12px; line-height: 1.6; color: rgba(255,255,255,.65); }
.foot { font-size: 12px; color: rgba(255,255,255,.45); margin-top: 32px; }
.right { display: flex; align-items: center; justify-content: center; padding: 40px; }
.box { width: 380px; }
.box h2 { font-size: 26px; margin: 0; }
.box p { margin: 8px 0 0; font-size: 13px; }
.full { width: 100%; }
.link { cursor: pointer; }
.or { display: flex; align-items: center; gap: 12px; margin: 20px 0; color: var(--jc-ink-4); font-size: 12px; }
.or::before, .or::after { content: ''; flex: 1; height: 1px; background: var(--jc-line); }
.demo { margin-top: 32px; padding-top: 20px; border-top: 1px dashed var(--jc-line); }
@media (max-width: 960px) { .login { grid-template-columns: 1fr; } .left { display: none; } }
</style>
