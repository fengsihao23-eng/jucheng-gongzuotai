<template>
  <Teleport to="body">
    <div v-if="deletionMenu" class="delete-menu-layer"
      @click.self="closeDeleteMenu" @contextmenu.self.prevent="closeDeleteMenu" @keydown.esc.prevent="closeDeleteMenu"
      @keydown.tab.prevent="closeDeleteMenu">
      <div class="delete-menu" role="menu" aria-label="记录操作"
        :style="{ left: deletionMenu.x + 'px', top: deletionMenu.y + 'px' }">
        <button ref="menuButton" type="button" role="menuitem" @click="deleteRecord(deletionMenu.descriptor)">
          {{ deletionMenu.descriptor.menuLabel || '删除' }}
        </button>
      </div>
    </div>
  </Teleport>
  <PasswordDeleteDialog v-if="deletionRequest" :model-value="open" :name="deletionRequest.name"
    :title="deletionRequest.title || '删除记录'" :description="deletionRequest.description"
    :confirm-label="deletionRequest.confirmLabel" :perform="perform"
    @update:model-value="open = $event" @deleted="completed" @closed="close" />
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import PasswordDeleteDialog from '@/views/operations/PasswordDeleteDialog.vue'
import { deletionMenu, deletionRequest, closeDeleteMenu, deleteRecord, submitDeletion } from '@/composables/useRecordDeletion'

const open = ref(false)
const menuButton = ref(null)
const route = useRoute()
watch(() => route.path, () => { deletionMenu.value = null })
watch(deletionMenu, async menu => { if (menu) { await nextTick(); menuButton.value?.focus() } })
watch(deletionRequest, request => { if (request) open.value = true })
function perform(password) { return submitDeletion(deletionRequest.value, password) }
async function completed(result) {
  try { await deletionRequest.value?.onDeleted?.(result) }
  catch { /* The request client reports refresh errors. */ }
}
function close() { deletionRequest.value = null }
</script>

<style scoped>
.delete-menu-layer { position: fixed; inset: 0; z-index: 4000; }
.delete-menu { position: absolute; min-width: 152px; padding: 5px; background: var(--jc-surface); border: 1px solid var(--jc-line); border-radius: 8px; box-shadow: 0 7px 24px #181c2922; }
.delete-menu button { width: 100%; appearance: none; background: transparent; border: 0; border-radius: 5px; text-align: left; font: inherit; color: var(--jc-down); padding: 8px 12px; cursor: pointer; }
.delete-menu button:hover, .delete-menu button:focus-visible { background: var(--jc-bg-2); }
</style>
