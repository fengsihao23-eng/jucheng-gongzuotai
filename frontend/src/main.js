import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './styles/theme.css'
import App from './App.vue'
import router from './router'
import components from './components'
import { installOperationAudit } from './utils/operationAudit'
import { recordDeleteDirective } from './composables/useRecordDeletion'

const app = createApp(App)
installOperationAudit(router)
app.use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).use(components)
app.directive('record-delete', recordDeleteDirective)
app.config.errorHandler = (err, vm, info) => {
  console.error('[vue]', info, err)
}
app.mount('#app')
