<!-- 员工自助答疑（全员可用，接口 POST /hr/qa）：考勤、年假、社保、请假规则；结合本人数据回答并给出出处。
  用法：<HrQaDialog v-model="open" /> 弹窗；<HrQaDialog inline /> 嵌入门户/个人中心卡片。 -->
<template>
  <component :is="inline ? 'div' : ElDialog" v-bind="inline ? {} : dialogProps" class="qa-root">
    <div class="qa">
      <div ref="box" class="msgs">
        <div v-if="!msgs.length" class="intro">
          <div class="jc-muted jc-small mb-8">可以这样问：</div>
          <div class="jc-row wrap">
            <span v-for="q in suggestions" :key="q" class="sg" @click="ask(q)">{{ q }}</span>
          </div>
        </div>
        <div v-for="(m, i) in msgs" :key="i" class="m" :class="m.role">
          <div class="bubble">
            <div class="txt">{{ m.text }}</div>
            <div v-if="m.sources?.length" class="src">
              <span class="jc-muted jc-small">出处：</span>
              <a v-for="(s, j) in m.sources" :key="j" :href="s.url" target="_blank" rel="noopener" class="jc-small">
                <span class="jc-cite">{{ j + 1 }}</span>{{ s.title }}{{ s.heading ? ' · ' + s.heading : '' }}
              </a>
            </div>
            <StatusPill v-if="m.personal" text="已结合你的个人数据" tone="brand" class="mt-8" />
          </div>
        </div>
        <div v-if="busy" class="m bot"><div class="bubble jc-muted">正在查询制度与个人数据…</div></div>
      </div>
      <div class="ipt">
        <el-input v-model="q" placeholder="输入问题，如：我还有几天年假？" :disabled="busy" @keyup.enter="ask()" />
        <el-button type="primary" :loading="busy" :disabled="!q.trim()" @click="ask()">发送</el-button>
      </div>
      <div class="jc-small jc-muted mt-8">答案依据公司制度与考勤数据自动生成，仅供参考；如有疑问请联系人事部。</div>
    </div>
  </component>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElDialog } from 'element-plus'
import api from '@/api/client'

const props = defineProps({ modelValue: Boolean, inline: Boolean })
const emit = defineEmits(['update:modelValue'])
const dialogProps = computed(() => ({
  modelValue: props.modelValue, title: '员工自助答疑', width: '560px', appendToBody: true,
  'onUpdate:modelValue': (v) => emit('update:modelValue', v),
}))
const q = ref('')
const msgs = ref([])
const busy = ref(false)
const box = ref()
const suggestions = ref(['我还有几天年假？', '我这个月迟到几次？', '病假怎么请？', '社保公积金怎么缴？', '加班可以调休吗？'])

async function ask(text) {
  const question = (text ?? q.value).trim()
  if (!question || busy.value) return
  msgs.value.push({ role: 'me', text: question })
  q.value = ''
  busy.value = true
  scroll()
  try {
    const r = await api.post('/hr/qa', { question })
    msgs.value.push({ role: 'bot', text: r.answer, sources: r.sources, personal: r.personal })
  } catch (e) {
    msgs.value.push({ role: 'bot', text: e.message || '暂时无法回答，请稍后再试。' })
  } finally {
    busy.value = false
    scroll()
  }
}
function scroll() { nextTick(() => { if (box.value) box.value.scrollTop = box.value.scrollHeight }) }
onMounted(async () => {
  try { suggestions.value = (await api.get('/hr/qa/suggestions', null, { silent: true })).questions } catch { /* 使用默认 */ }
})
</script>

<style scoped>
.qa { display: flex; flex-direction: column; }
.msgs { min-height: 260px; max-height: 420px; overflow-y: auto; padding: 4px 2px 12px; }
.intro { padding: 8px 0; }
.sg { font-size: 12.5px; padding: 4px 10px; border: 1px solid var(--jc-line); border-radius: 14px; cursor: pointer; color: var(--jc-ink-2); background: var(--jc-surface-2); }
.sg:hover { border-color: var(--jc-brand); color: var(--jc-brand); }
.m { display: flex; margin: 10px 0; }
.m.me { justify-content: flex-end; }
.bubble { max-width: 86%; padding: 10px 14px; border-radius: 10px; font-size: 13.5px; line-height: 1.7; }
.me .bubble { background: var(--jc-brand); color: #fff; border-bottom-right-radius: 3px; }
.bot .bubble { background: var(--jc-surface-2); border: 1px solid var(--jc-line-2); border-bottom-left-radius: 3px; }
.txt { white-space: pre-line; }
.src { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--jc-line); display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; }
.ipt { display: flex; gap: 8px; border-top: 1px solid var(--jc-line-2); padding-top: 12px; }
</style>
