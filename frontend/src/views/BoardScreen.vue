<!-- FR-03-06 经营大屏：全屏无侧栏，深藏青底（无霓虹/发光），每 5 分钟自动刷新，板块轮播高亮 -->
<template>
  <div class="screen">
    <header class="hd">
      <div class="l">
        <JcLogo :size="28" inverse :with-text="false" />
        <h1 class="jc-serif">聚成数智 · 经营大屏</h1>
      </div>
      <div class="r">
        <span class="jc-num clock">{{ clock }}</span>
        <span class="muted">更新于 {{ ov ? datetime(ov._cached_at) : '—' }} · 每 5 分钟刷新</span>
        <button class="btn" @click="paused = !paused">{{ paused ? '继续轮播' : '暂停轮播' }}</button>
        <button class="btn" @click="$router.push('/board')">返回</button>
      </div>
    </header>

    <main v-if="ov" class="body">
      <section class="kpis">
        <div class="k" :class="{ hl: active === 0 }"><span>当月回款</span><b class="jc-num">{{ wanNum(pay.amount) }}<i>万</i></b>
          <div class="pg"><em :style="{ width: Math.min(100, num(pay.rate) * 100) + '%' }" /></div>
          <small>目标 {{ wan(pay.target) }} · 达成 {{ percent(pay.rate) }}</small></div>
        <div class="k" :class="{ hl: active === 2 }"><span>今日签约</span><b class="jc-num">{{ wanNum(sign.today.amount) }}<i>万</i></b>
          <small>{{ sign.today.count }} 份 · 本月 {{ wan(sign.month.amount) }}</small></div>
        <div class="k" :class="{ hl: active === 2 }"><span>年度累计业绩</span><b class="jc-num">{{ wanNum(sign.year.amount, 0) }}<i>万</i></b>
          <small>同比 {{ percent(sign.year.yoy) }}</small></div>
        <div class="k" :class="{ hl: active === 3 }"><span>本月新增商机</span><b class="jc-num">{{ opp.month.count }}<i>个</i></b>
          <small>{{ wan(opp.month.amount) }} · 本周 {{ opp.week.count }} 个</small></div>
        <div class="k" :class="{ hl: active === 4 }"><span>招标 / 在建</span><b class="jc-num">{{ tender.bidding_count }} / {{ tender.building_count }}</b>
          <small>节点预警 {{ tender.alert_count }} · 两周内截标 {{ tender.deadline_soon_count }}</small></div>
      </section>

      <section class="grid">
        <div class="card" :class="{ hl: active === 0 }">
          <h3>① 回款目标达成 <small>{{ pay.period }}</small></h3>
          <Chart :option="lineBarOption(pay, true)" height="230px" />
        </div>
        <div class="card" :class="{ hl: active === 0 }">
          <h3>每日累计回款 vs 目标线</h3>
          <Chart :option="dailyOption(pay, true)" height="230px" />
        </div>
        <div class="card" :class="{ hl: active === 3 }">
          <h3>④ 商机漏斗</h3>
          <Chart :option="funnelOption(opp, true)" height="230px" />
        </div>
        <div class="card wide" :class="{ hl: active === 1 }">
          <h3>② 重点项目进展 <small>延期 ▲ {{ kp.delayed_count }} / {{ kp.count }}</small></h3>
          <table class="tb">
            <thead><tr><th>项目</th><th>当前节点</th><th>状态</th><th>最新进展</th></tr></thead>
            <tbody>
              <tr v-for="r in kp.items" :key="r.project_code" :class="{ alert: r.delayed }">
                <td>{{ r.name }}</td><td>{{ r.current_node }}</td>
                <td><span :class="r.delayed ? 'down' : 'up'">{{ r.delayed ? '延期 ▲' : '正常 ●' }}</span></td>
                <td class="fb">{{ r.latest_feedback?.content || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card" :class="{ hl: active === 5 }">
          <h3>⑥ 本周周报提交率 <small>{{ percent(sub.weekly.rate) }}</small></h3>
          <Chart :option="submitOption(sub, true)" height="230px" />
        </div>
        <div class="card" :class="{ hl: active === 2 }">
          <h3>③ 当月签约 <small>{{ wan(sign.month.amount) }}</small></h3>
          <ul class="ls">
            <li v-for="c in sign.items.slice(0, 7)" :key="c.id"><span>{{ shortDate(c.sign_date) }} {{ c.customer_name }}</span><b class="jc-num">{{ wan(c.amount) }}</b></li>
          </ul>
        </div>
        <div class="card" :class="{ hl: active === 4 }">
          <h3>⑤ 招标截止 / 节点预警</h3>
          <ul class="ls">
            <li v-for="d in tender.deadlines.slice(0, 4)" :key="d.project_code"><span>{{ d.name }}</span><b :class="{ down: d.urgent }">剩 {{ d.days_left }} 天</b></li>
            <li v-for="a in tender.alerts.slice(0, 3)" :key="a.project_code + a.node_name"><span>{{ a.project_name }} · {{ a.node_name }}</span>
              <b :class="a.type === 'overdue' ? 'down' : 'warn'">{{ a.type === 'overdue' ? '超期' : a.days + ' 天' }}</b></li>
          </ul>
        </div>
        <div class="card">
          <h3>毛利摘要</h3>
          <div class="pf"><div><span>本周毛利</span><b class="jc-num">{{ wan(ov.profit.week_gross_profit) }}</b></div>
            <div><span>累计毛利率</span><b class="jc-num">{{ percent(ov.profit.gross_margin) }}</b></div></div>
        </div>
      </section>
    </main>
    <div v-else class="loading">加载中…</div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import api from '@/api/client'
import { datetime, num, percent, shortDate, wan, wanNum } from '@/utils/format'
import { dailyOption, funnelOption, lineBarOption, submitOption } from './board-charts'

const ov = ref(null)
const active = ref(0)
const paused = ref(false)
const clock = ref(dayjs().format('YYYY-MM-DD HH:mm:ss'))
const pay = computed(() => ov.value.payment)
const sign = computed(() => ov.value.sign)
const opp = computed(() => ov.value.opportunity)
const tender = computed(() => ov.value.tender)
const sub = computed(() => ov.value.submission)
const kp = computed(() => ov.value.key_projects)
let t1, t2, t3

async function load() {
  try { ov.value = await api.get('/board/overview', { refresh: 0 }, { silent: !!ov.value }) } catch { /* 保留上次数据 */ }
}
onMounted(() => {
  load()
  t1 = setInterval(load, 5 * 60 * 1000)
  t2 = setInterval(() => { if (!paused.value) active.value = (active.value + 1) % 6 }, 8000)
  t3 = setInterval(() => (clock.value = dayjs().format('YYYY-MM-DD HH:mm:ss')), 1000)
})
onBeforeUnmount(() => { clearInterval(t1); clearInterval(t2); clearInterval(t3) })
</script>

<style scoped>
.screen { min-height: 100vh; background: #14234f; color: #e8ecf6; display: flex; flex-direction: column; padding: 18px 24px; }
.hd { display: flex; justify-content: space-between; align-items: center; padding-bottom: 14px; border-bottom: 1px solid rgba(255,255,255,0.12); }
.hd .l { display: flex; align-items: center; gap: 12px; }
.hd h1 { margin: 0; font-size: 24px; color: #fff; }
.hd .r { display: flex; align-items: center; gap: 14px; }
.clock { font-size: 18px; }
.muted { color: #9fadd0; font-size: 12px; }
.btn { background: transparent; color: #e8ecf6; border: 1px solid rgba(255,255,255,0.28); border-radius: 6px; padding: 5px 12px; cursor: pointer; font: inherit; font-size: 13px; }
.btn:hover { border-color: #e8ecf6; }
.body { flex: 1; display: flex; flex-direction: column; gap: 14px; padding-top: 14px; min-height: 0; }
.kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; }
.k, .card { background: #1b2d63; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; transition: border-color 0.4s, background 0.4s; }
.k.hl, .card.hl { border-color: #B0894F; background: #1f3370; }
.k { padding: 14px 18px; display: flex; flex-direction: column; gap: 6px; }
.k span { color: #9fadd0; font-size: 13px; }
.k b { font-size: 38px; font-weight: 600; color: #fff; line-height: 1.1; }
.k b i { font-style: normal; font-size: 14px; color: #9fadd0; margin-left: 4px; }
.k small { color: #9fadd0; font-size: 12px; }
.pg { height: 5px; background: rgba(255,255,255,0.12); border-radius: 3px; overflow: hidden; }
.pg em { display: block; height: 100%; background: #e8ecf6; }
.grid { flex: 1; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-auto-rows: minmax(250px, 1fr); gap: 14px; }
.card { padding: 12px 16px; display: flex; flex-direction: column; min-height: 0; }
.card.wide { grid-column: span 2; }
.card h3 { margin: 0 0 8px; font-size: 15px; font-weight: 600; color: #fff; }
.card h3 small { color: #9fadd0; font-weight: 400; margin-left: 6px; }
.ch { flex: 1; min-height: 200px; }
.tb { width: 100%; border-collapse: collapse; font-size: 13px; }
.tb th { text-align: left; color: #9fadd0; font-weight: 400; padding: 6px 8px; border-bottom: 1px solid rgba(255,255,255,0.12); }
.tb td { padding: 7px 8px; border-bottom: 1px solid rgba(255,255,255,0.06); }
.tb tr.alert td { background: rgba(166,61,50,0.16); }
.tb .fb { color: #c9d1e6; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.down { color: #e8a598; } .up { color: #9fd0b5; } .warn { color: #e2c48e; }
.ls { list-style: none; margin: 0; padding: 0; font-size: 13px; }
.ls li { display: flex; justify-content: space-between; gap: 10px; padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.ls li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #c9d1e6; }
.pf { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 8px; }
.pf div { display: flex; flex-direction: column; gap: 6px; }
.pf span { color: #9fadd0; font-size: 13px; }
.pf b { font-size: 30px; color: #fff; }
.loading { flex: 1; display: flex; align-items: center; justify-content: center; color: #9fadd0; }
</style>
