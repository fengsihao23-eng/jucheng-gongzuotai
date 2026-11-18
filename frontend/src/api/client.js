// 统一 HTTP 客户端：解包 {code,message,data}；业务错误统一提示；令牌滑动续期；文件下载。
import axios from 'axios'
import { ElMessage } from 'element-plus'

const TOKEN_KEY = 'jc_token'
export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY))

const http = axios.create({ baseURL: '/api/v1', timeout: 60000 })

http.interceptors.request.use((cfg) => {
  const t = getToken()
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

http.interceptors.response.use(
  (resp) => {
    const refreshed = resp.headers['x-refresh-token']
    if (refreshed) setToken(refreshed)
    if (resp.config.responseType === 'blob') return resp
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) return body.data
      return Promise.reject(bizError(body, resp.config))
    }
    return body
  },
  async (err) => {
    let body = err.response?.data
    if (body instanceof Blob && body.type.includes('json')) {
      try { body = JSON.parse(await body.text()) } catch { /* 保留网络错误提示 */ }
    }
    if (body && typeof body === 'object' && 'code' in body) return Promise.reject(bizError(body, err.config))
    const e = new Error(err.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '网络异常，请检查连接')
    if (!err.config?.silent) ElMessage.error(e.message)
    return Promise.reject(e)
  },
)

function bizError(body, cfg) {
  const e = new Error(body.message || '操作失败')
  e.code = body.code
  e.data = body.data
  e.traceId = body.trace_id
  const isLoginCall = typeof cfg?.url === 'string' && (cfg.url.includes('/auth/login') || cfg.url.includes('/auth/wecom/oauth'))
  if ((body.code === 40101 || body.code === 40102) && !isLoginCall) {
    setToken(null)
    if (!location.pathname.startsWith('/login')) {
      location.href = '/login?redirect=' + encodeURIComponent(location.pathname + location.search)
    }
  } else if (!cfg?.silent) {
    ElMessage.error(e.message)
  }
  return e
}

export const api = {
  get: (url, params, cfg = {}) => http.get(url, { params, ...cfg }),
  post: (url, data, cfg = {}) => http.post(url, data, cfg),
  put: (url, data, cfg = {}) => http.put(url, data, cfg),
  patch: (url, data, cfg = {}) => http.patch(url, data, cfg),
  del: (url, params, cfg = {}) => http.delete(url, { params, ...cfg }),
  /** multipart 上传：fields 为附加表单字段 */
  upload: (url, file, fields = {}, cfg = {}) => {
    const fd = new FormData()
    fd.append('file', file)
    Object.entries(fields).forEach(([k, v]) => v !== undefined && v !== null && fd.append(k, v))
    return http.post(url, fd, { timeout: 180000, ...cfg })
  },
  /** 以当前登录身份下载接口返回的二进制（如导出 xlsx） */
  download: async (url, params, filename) => {
    const resp = await http.get(url, { params, responseType: 'blob' })
    if (resp.data.type?.includes('json') && !resp.headers['content-disposition']) {
      const body = JSON.parse(await resp.data.text())
      if (body && typeof body === 'object' && 'code' in body && body.code !== 0) {
        ElMessage.error(body.message || '导出失败')
        throw new Error(body.message)
      }
    }
    const cd = resp.headers['content-disposition'] || ''
    const m = cd.match(/filename\*=UTF-8''([^;]+)/) || cd.match(/filename="?([^";]+)"?/)
    const name = filename || (m ? decodeURIComponent(m[1]) : 'download')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(resp.data)
    a.download = name
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  },
}

/** 打开后端签名链接（报表/归档文件），url 来自接口返回的 file.url */
export function openFile(url) {
  if (!url) return
  const target = new URL(url, window.location.origin)
  if (target.origin === window.location.origin && target.pathname.startsWith('/api/v1/files/')) {
    // 原件下载必须携带本人登录态，复制签名链接不能绕过文档权限。
    return api.download(target.pathname.slice('/api/v1'.length) + target.search).catch(() => {})
  }
  window.open(url, '_blank', 'noopener')
}

export default api
