import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './styles/theme.css'
import App from './App.vue'
import router from './router'
import components from './components'

const app = createApp(App)
app.use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).use(components)
app.config.errorHandler = (err, vm, info) => {
  console.error('[vue]', info, err)
}
app.mount('#app')
