<template>
  <el-button v-if="project.schools.length" @click="show">生成学校方案初稿</el-button>
  <el-dialog v-model="open" title="学校方案初稿" width="min(760px, 94vw)" append-to-body>
    <el-form label-position="top">
      <el-form-item label="学校">
        <el-select v-model="school" filterable><el-option v-for="s in project.schools" :key="s.id" :label="s.name" :value="s.id" /></el-select>
      </el-form-item>
      <el-form-item label="建设目标与现场需求"><el-input v-model="need" type="textarea" :rows="2" maxlength="1000" /></el-form-item>
      <el-form-item label="从产品库选择设备和数量">
        <div v-for="(line, index) in lines" :key="index" class="product-line">
          <el-select v-model="line.product_id" filterable placeholder="产品型号">
            <el-option v-for="r in products" :key="r.id" :value="r.id" :label="[r.name, r.brand, r.model].filter(Boolean).join(' · ')" />
          </el-select>
          <el-input-number v-model="line.quantity" :min="1" :max="100000" :precision="0" aria-label="数量" />
          <el-button link @click="lines.splice(index, 1)">移除</el-button>
        </div>
        <el-button link type="primary" :disabled="lines.length >= 40" @click="lines.push({ product_id: null, quantity: 1 })">添加设备</el-button>
      </el-form-item>
    </el-form>
    <div v-if="result">
      <el-input v-model="draft" type="textarea" :rows="12" aria-label="可编辑方案初稿" />
      <p v-if="result.sources.length" class="jc-small jc-muted">知识库参考：{{ result.sources.map(s => s.title).join('、') }}</p>
    </div>
    <template #footer>
      <el-button v-if="result" @click="assistant.openDraft('请基于以下已核对的设备和项目需求完善方案。保留待核实事项，不编造规格和价格。\n\n' + draft); open = false">用小聚继续编辑</el-button>
      <el-button type="primary" :loading="loading" :disabled="!school || need.trim().length < 4 || !lines.length || lines.some(l => !l.product_id)" @click="generate">生成初稿</el-button>
    </template>
  </el-dialog>
</template>
<script setup>
import { ref } from 'vue'
import api from '@/api/client'
import { useAssistant } from '@/store/assistant'
const props = defineProps({ project: Object }), assistant = useAssistant()
const open = ref(false), school = ref(null), need = ref(''), products = ref([]), lines = ref([]), result = ref(null), draft = ref(''), loading = ref(false)
async function show() {
  open.value = true
  if (!lines.value.length) lines.value = [{ product_id: null, quantity: 1 }]
  try { products.value = (await api.get('/operations/products')).items } catch {}
}
async function generate() {
  loading.value = true
  try {
    result.value = await api.post('/operations/' + props.project.id + '/proposal', { school_id: school.value, need: need.value, products: lines.value })
    draft.value = result.value.text
  } catch {} finally { loading.value = false }
}
</script>
<style scoped>.product-line { display: flex; flex-wrap: wrap; gap: 8px; width: 100%; margin-bottom: 6px; }.product-line .el-select { flex: 1 1 210px; }</style>
