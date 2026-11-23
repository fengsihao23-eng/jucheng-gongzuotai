<template><slot /></template>

<script setup>
import { provide } from 'vue'
import { routeLocationKey } from 'vue-router'

const props = defineProps({ location: { type: Object, required: true } })
// Keep list filters bound to the list URL while the detail URL is in the address bar.
const location = {}
for (const key of Object.keys(props.location)) {
  Object.defineProperty(location, key, { enumerable: true, get: () => props.location[key] })
}
provide(routeLocationKey, location)
</script>
