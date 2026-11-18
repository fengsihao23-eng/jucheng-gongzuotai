<template>
  <router-view :key="auth.profile?.permission_revision || 'initial'" />
</template>

<script setup>
import { useAuth } from '@/store/auth'
import { usePermissionRefresh } from '@/composables/usePermissionRefresh'
const auth = useAuth()
usePermissionRefresh()
</script>
