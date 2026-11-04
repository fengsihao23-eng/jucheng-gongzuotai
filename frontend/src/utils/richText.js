import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import sql from 'highlight.js/lib/languages/sql'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import yaml from 'highlight.js/lib/languages/yaml'

for (const [name, language] of Object.entries({ javascript, typescript, json, python, bash, sql, xml, css, yaml })) {
  hljs.registerLanguage(name, language)
}

// 原生 HTML 始终作为文字显示；链接继续使用 MarkdownIt 的协议校验。
const md = new MarkdownIt({ html: false, linkify: true, breaks: false, typographer: false })
const escape = md.utils.escapeHtml
const copyIcon = '<svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="7" y="7" width="9" height="10" rx="2"/><path d="M12 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/></svg>'
const copyButton = (kind, label) => `<button type="button" class="md-copy" data-copy="${kind}" aria-label="${label}" title="${label}">${copyIcon}<span>${label}</span></button>`

md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const language = token.info.trim().split(/\s+/)[0].toLowerCase()
  let code = escape(token.content)
  // 大段纯文本无需高亮，避免流式回复期间重复解析过多内容。
  if (language && hljs.getLanguage(language) && token.content.length < 50000) {
    code = hljs.highlight(token.content, { language, ignoreIllegals: true }).value
  }
  return `<section class="md-code"><div class="md-code-bar"><span>${escape(language || 'text')}</span>${copyButton('code', '复制代码')}</div><pre tabindex="0"><code class="hljs">${code}</code></pre></section>\n`
}

md.renderer.rules.table_open = () => `<section class="md-table"><div class="md-table-scroll" tabindex="0" role="region" aria-label="数据表格，可左右滚动"><table>\n`
md.renderer.rules.table_close = () => `</table></div><div class="md-table-bar">${copyButton('table', '复制表格')}</div></section>\n`

const defaultLink = md.renderer.rules.link_open || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLink(tokens, idx, options, env, self)
}

// 远程图片以可点击链接展示，避免模型输出导致浏览器自动请求外部资源。
md.renderer.rules.image = (tokens, idx) => {
  const token = tokens[idx]
  return `<a href="${escape(token.attrGet('src') || '')}" target="_blank" rel="noopener noreferrer">${escape(token.content || '查看图片')}</a>`
}

export function richText(text) {
  return md.render(String(text ?? '').replace(/\r\n?/g, '\n'))
}
