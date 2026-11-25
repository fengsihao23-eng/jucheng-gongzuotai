<template>
  <el-select v-model="category" aria-label="归档分类" placeholder="归档分类" clearable :value-on-clear="''">
    <el-option value="unarchived" label="未归档" />
    <el-option v-for="(label, value) in ARCHIVE_CATEGORIES" :key="value" :value="value" :label="label" />
  </el-select>
</template>

<script setup>
import { ARCHIVE_CATEGORIES } from '@/utils/workbookArchive'

const category = defineModel({ type: String, required: true })
</script>
