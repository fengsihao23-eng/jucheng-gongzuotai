<template>
  <router-view />
</template>

<script setup>
import { useAuth } from '@/store/auth'
import { usePermissionRefresh } from '@/composables/usePermissionRefresh'
const auth = useAuth()
usePermissionRefresh()
</script>
