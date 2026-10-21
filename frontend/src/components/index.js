// 全局注册共享组件，页面中可直接使用 <PageHeader> <Panel> <Kpi> <Chart> <FileLink> <UploadBox> <StatusPill> <JcLogo>
import PageHeader from './PageHeader.vue'
import Panel from './Panel.vue'
import Kpi from './Kpi.vue'
import Chart from './Chart.vue'
import FileLink from './FileLink.vue'
import UploadBox from './UploadBox.vue'
import StatusPill from './StatusPill.vue'
import JcLogo from './JcLogo.vue'

export default {
  install(app) {
    Object.entries({ PageHeader, Panel, Kpi, Chart, FileLink, UploadBox, StatusPill, JcLogo }).forEach(([n, c]) =>
      app.component(n, c),
    )
  },
}
