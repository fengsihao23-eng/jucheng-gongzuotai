<template>
  <router-view />
  <RecordDeletionOutlet />
</template>

<script setup>
import { useAuth } from '@/store/auth'
import { usePermissionRefresh } from '@/composables/usePermissionRefresh'
import RecordDeletionOutlet from '@/components/records/RecordDeletionOutlet.vue'
const auth = useAuth()
usePermissionRefresh()
</script>
