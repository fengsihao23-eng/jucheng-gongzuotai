// 小聚回复的轻量排版：先整体转义，再只放行少量格式（粗体、行内代码、链接、列表符号）。不解析 HTML，防止注入。
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c])

export function richText(text) {
  const lines = esc(text).split('\n')
  const out = []
  let list = null
  const flush = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${i}</li>`).join('')}</${list.tag}>`); list = null } }
  for (const raw of lines) {
    const line = inline(raw)
    const ul = raw.match(/^\s*[-•·*]\s+(.*)$/)
    const ol = raw.match(/^\s*(\d{1,2})[.、)]\s+(.*)$/)
    if (ul || ol) {
      const tag = ul ? 'ul' : 'ol'
      if (!list || list.tag !== tag) { flush(); list = { tag, items: [] } }
      list.items.push(inline(ul ? ul[1] : ol[2]))
      continue
    }
    flush()
    const h = raw.match(/^#{1,4}\s+(.*)$/)
    if (h) out.push(`<h4>${inline(h[1])}</h4>`)
    else if (line.trim()) out.push(`<p>${line}</p>`)
  }
  flush()
  return out.join('')
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\b(https?:\/\/[^\s<]+[^\s<.,;:!?，。；：！？）)])/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>')
}
