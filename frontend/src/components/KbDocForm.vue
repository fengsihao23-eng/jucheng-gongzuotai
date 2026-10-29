<!-- 知识库文档信息表单：标题 / 两级分类 / 标签 / 摘要 / 可见范围。上传、编辑、导入确认共用。 -->
<template>
  <el-form label-width="84px" class="kbf">
    <el-form-item label="标题" required>
      <el-input v-model="model.title" maxlength="200" placeholder="默认取文件名" />
    </el-form-item>
    <el-form-item label="分类" required>
      <div class="jc-row gap">
        <el-select v-model="model.category" placeholder="一级分类" style="width: 160px" @change="model.subcategory = ''">
          <el-option v-for="c in tree.filter((x) => x.id)" :key="c.id" :label="c.name" :value="c.name" />
        </el-select>
        <el-select v-model="model.subcategory" placeholder="二级分类（可选）" clearable style="width: 180px" :disabled="!subs.length">
          <el-option v-for="s in subs" :key="s.id" :label="s.name" :value="s.name" />
        </el-select>
      </div>
      <div v-if="suggestion?.category" class="jc-small jc-muted hint">
        {{ suggestion.source === 'llm' ? 'AI 建议' : '系统建议' }}：{{ suggestion.category }}{{ suggestion.subcategory ? ' / ' + suggestion.subcategory : '' }}（{{ suggestion.reason }}）
      </div>
      <div v-if="reviewHint" class="jc-small jc-muted hint">{{ reviewHint }}</div>
    </el-form-item>
    <el-form-item label="标签">
      <el-select v-model="model.tags" multiple filterable allow-create default-first-option :multiple-limit="10"
        placeholder="输入后回车，如 业务线、年份、客户" style="width: 100%">
        <el-option v-for="t in TAG_HINTS" :key="t" :label="t" :value="t" />
      </el-select>
    </el-form-item>
    <el-form-item label="摘要">
      <el-input v-model="model.summary" type="textarea" :rows="2" maxlength="500" placeholder="一两句话说明这份文档讲什么、适用于什么场景（可选）" />
    </el-form-item>
    <el-form-item label="可见范围">
      <el-radio-group v-model="model.visibility">
        <el-radio value="all">全员</el-radio>
        <el-radio value="dept">指定部门</el-radio>
        <el-radio value="roles">指定角色</el-radio>
      </el-radio-group>
      <el-select v-if="model.visibility === 'dept'" v-model="model.visible_depts" multiple placeholder="选择部门" class="mt-8" style="width: 100%">
        <el-option v-for="d in meta.departments || []" :key="d.code" :label="d.name" :value="d.code" />
      </el-select>
      <el-select v-if="model.visibility === 'roles'" v-model="model.visible_roles" multiple filterable placeholder="选择角色" class="mt-8" style="width: 100%">
        <el-option v-for="r in meta.roles || []" :key="r.code" :label="`${r.name}（${r.code}）`" :value="r.code" />
      </el-select>
    </el-form-item>
  </el-form>
</template>

<script setup>
import { computed } from 'vue'

const TAG_HINTS = ['数字校园', '综合智慧安防', '素质教育考评', '运维服务', '2026', '2025', '投标', '内部']
const model = defineModel({ type: Object, required: true })
const props = defineProps({ tree: { type: Array, default: () => [] }, meta: { type: Object, default: () => ({}) },
  suggestion: Object, roles: { type: Array, default: () => [] } })
const subs = computed(() => props.tree.find((c) => c.name === model.value.category)?.children || [])
const reviewHint = computed(() => {
  const c = props.tree.find((x) => x.name === model.value.category)
  if (!c) return ''
  if (props.meta.can_manage || c.reviewer_roles.some((r) => props.roles.includes(r))) return '你是该分类审核人，提交后直接发布'
  return '提交后需该分类审核人审核才会发布'
})
</script>

<style scoped>
.gap { gap: 8px; }
.hint { margin-top: 4px; line-height: 1.5; }
.mt-8 { margin-top: 8px; }
</style>
