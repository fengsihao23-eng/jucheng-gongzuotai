<template>
  <div class="shell" :class="{ 'operations-layout': route.meta.menu === 'operations', 'admin-layout': route.meta.menu === 'admin', 'personal-work-layout': route.name === 'portal', 'workbook-layout': !!route.meta.workbookView, 'key-project-layout': route.path.startsWith('/market') || route.name === 'receivables' }">
    <aside class="side jc-noprint">
      <router-link to="/" class="brand"><JcLogo :size="30" /></router-link>
      <nav>
        <template v-for="g in groups" :key="g.title">
          <div v-if="g.title" class="gt">{{ g.title }}</div>
          <router-link v-for="m in g.items" :key="m.path" :to="m.path" class="item"
                       :class="{ active: isActive(m) }">
            <span class="bar" />{{ m.label }}
          </router-link>
        </template>
      </nav>
      <div class="side-foot jc-muted jc-small">
        <div v-if="route.meta.workbookView">数据来源：见页面同步状态</div>
        <div v-else>数据模式：{{ mode === 'mock' ? '演示数据' : '生产数据' }}</div>
        <div>v2.0 · 广东聚成数智</div>
      </div>
    </aside>

    <div class="main">
      <header class="top jc-noprint">
        <div class="crumb">
          <span class="jc-muted">{{ profile?.dept_name }}</span>
          <span class="sep">/</span>
          <span>{{ route.meta.title }}</span>
        </div>
        <div class="jc-spacer" />
        <span class="jc-muted jc-small today">{{ today }}</span>
        <el-button text @click="router.push('/account')">修改密码</el-button>
        <el-button class="assistant-entry" @click="assistant.open()">小聚</el-button>
        <el-popover placement="bottom-end" :width="360" trigger="click" @show="loadNotify">
          <template #reference>
            <button class="icon-btn" aria-label="消息通知">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
                <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
              </svg>
              <i v-if="unread" class="badge jc-num">{{ unread > 99 ? '99+' : unread }}</i>
            </button>
          </template>
          <div class="np">
            <div class="jc-row between np-hd">
              <b>消息通知</b>
              <el-button link size="small" @click="readAll">全部已读</el-button>
            </div>
            <div v-if="!notices.length" class="jc-muted np-empty">暂无消息</div>
            <div v-for="n in notices" :key="n.id" class="np-item" :class="{ unread: !n.read }" @click="openNotice(n)">
              <div class="jc-row between"><b class="jc-ellipsis">{{ n.title }}</b><span class="jc-muted jc-small">{{ fromNow(n.created_at) }}</span></div>
              <div class="jc-muted jc-small np-c">{{ n.content }}</div>
            </div>
            <router-link to="/notifications" class="np-more">查看全部</router-link>
          </div>
        </el-popover>
        <el-dropdown trigger="click" @command="onCmd">
          <div class="me">
            <span class="avatar">{{ profile?.name?.slice(-2) }}</span>
            <div class="me-t">
              <b>{{ profile?.name }}</b>
              <small class="jc-muted">{{ profile?.roles?.map((r) => r.name).join(' / ') }}</small>
            </div>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="account">个人中心</el-dropdown-item>
              <el-dropdown-item command="workbuddy">WorkBuddy 接入</el-dropdown-item>
              <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </header>
      <main class="content">
        <router-view v-slot="{ Component }">
          <component :is="Component" :key="route.fullPath.split('?')[0]" />
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import api from '@/api/client'
import { useAuth } from '@/store/auth'
import { MENU_GROUPS } from '@/config/menu'
import { fromNow } from '@/utils/format'
import { useAssistant } from '@/store/assistant'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const assistant = useAssistant()
const profile = computed(() => auth.profile)
const mode = ref('mock')
const unread = ref(0)
const notices = ref([])
const W = ['日', '一', '二', '三', '四', '五', '六']
const today = dayjs().format('YYYY年M月D日') + ' 星期' + W[dayjs().day()]

const groups = computed(() =>
  MENU_GROUPS.map((g) => ({ ...g, items: g.items.filter((m) => (!m.adminOnly || profile.value?.is_admin) && (m.anyMenu || [m.key]).some(key => auth.hasMenu(key))) })).filter((g) => g.items.length),
)
const isActive = (m) => (m.path === '/' ? route.path === '/' : route.path === m.path || route.path.startsWith(m.path + '/'))

async function pollUnread() {
  try {
    const r = await api.get('/notifications', { page_size: 1 }, { silent: true })
    unread.value = r.unread
  } catch { /* ignore */ }
}
async function loadNotify() {
  const r = await api.get('/notifications', { page_size: 8 })
  notices.value = r.items
  unread.value = r.unread
}
async function readAll() {
  await api.post('/notifications/read', {})
  await loadNotify()
}
async function openNotice(n) {
  if (!n.read) await api.post('/notifications/read', { ids: [n.id] })
  pollUnread()
  if (n.link) router.push(n.link)
}
async function onCmd(c) {
  if (c === 'logout') {
    await auth.logout()
    router.replace('/login')
  } else router.push('/' + c)
}

let timer
onMounted(async () => {
  pollUnread()
  timer = setInterval(pollUnread, 60000)
  try { mode.value = (await fetch('/health').then((r) => r.json())).mode } catch { /* ignore */ }
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.side {
  width: var(--jc-sidebar-w); flex-shrink: 0; background: var(--jc-surface); border-right: 1px solid var(--jc-line);
  position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column;
}
.brand { display: block; padding: 16px 18px 12px; border-bottom: 1px solid var(--jc-line-2); text-decoration: none; }
nav { flex: 1; overflow-y: auto; padding: 8px 10px 16px; }
.gt { font-size: 11px; color: var(--jc-ink-4); letter-spacing: 0.12em; padding: 16px 10px 6px; }
.item {
  position: relative; display: flex; align-items: center; height: 36px; padding: 0 12px; border-radius: 6px;
  color: var(--jc-ink-2); font-size: 14px; text-decoration: none; transition: background 0.12s;
}
.item:hover { background: var(--jc-surface-2); text-decoration: none; }
.item .bar { position: absolute; left: 0; top: 9px; bottom: 9px; width: 2px; border-radius: 1px; background: transparent; }
.item.active { background: var(--jc-brand-50); color: var(--jc-brand); font-weight: 500; }
.item.active .bar { background: var(--jc-brand); }
.side-foot { padding: 12px 18px; border-top: 1px solid var(--jc-line-2); line-height: 1.7; }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.top {
  height: var(--jc-header-h); display: flex; align-items: center; gap: 16px;
  /* 左右内边距跟随 .jc-page 内容列：宽屏时内容居中于 1600px，顶栏同步内缩，面包屑与页面标题左边缘保持对齐 */
  padding: 0 max(var(--jc-page-pad), calc((100% - var(--jc-page-max)) / 2 + var(--jc-page-pad)));
  background: rgba(246, 244, 239, 0.92); backdrop-filter: saturate(1.2) blur(6px);
  border-bottom: 1px solid var(--jc-line); position: sticky; top: 0; z-index: 20;
}
.crumb { font-size: 13px; }
.sep { margin: 0 8px; color: var(--jc-ink-4); }
.icon-btn { position: relative; border: 0; background: transparent; color: var(--jc-ink-2); cursor: pointer; padding: 6px; border-radius: 6px; display: flex; }
.icon-btn:hover { background: var(--jc-surface); }
.badge { position: absolute; top: 0; right: -4px; min-width: 16px; height: 16px; padding: 0 4px; border-radius: 8px; background: var(--jc-down); color: #fff; font-size: 10px; font-style: normal; line-height: 16px; text-align: center; }
.me { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: var(--jc-brand); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 12px; }
.me-t { display: flex; flex-direction: column; line-height: 1.2; max-width: 180px; }
.me-t b { font-size: 13px; font-weight: 500; }
.me-t small { font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.content { flex: 1; min-width: 0; }

.np-hd { padding-bottom: 8px; border-bottom: 1px solid var(--jc-line-2); }
.np-empty { padding: 24px 0; text-align: center; }
.np-item { padding: 10px 4px; border-bottom: 1px solid var(--jc-line-2); cursor: pointer; }
.np-item:hover { background: var(--jc-surface-2); }
.np-item.unread b::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--jc-down); margin-right: 6px; vertical-align: 2px; }
.np-item b { font-size: 13px; font-weight: 500; max-width: 240px; }
.np-c { margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-line; }
.np-more { display: block; text-align: center; padding-top: 10px; font-size: 13px; }
@media (max-width: 900px) { .side { display: none; } .today { display: none; } .top { padding: 0 16px; } }
@media (max-width: 560px) {
  :is(.operations-layout, .personal-work-layout, .workbook-layout, .key-project-layout, .admin-layout) .top { gap: 8px; }
  :is(.operations-layout, .personal-work-layout, .workbook-layout, .key-project-layout, .admin-layout) .me-t, :is(.operations-layout, .personal-work-layout, .workbook-layout, .key-project-layout, .admin-layout) .top .jc-spacer { display: none; }
  :is(.operations-layout, .personal-work-layout, .workbook-layout, .key-project-layout, .admin-layout) .crumb { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}
</style>
