import test from 'node:test'
import assert from 'node:assert/strict'
import { richText } from '../src/utils/richText.js'

test('同类记录渲染成表格，保留数字和列对齐', () => {
  const html = richText('## 合同明细\n\n共 **5 份**，合计 **138,422 元**。\n\n| 合同 | 金额（元） |\n| --- | ---: |\n| HT-0053 | 56,500 |')
  assert.match(html, /<h2>合同明细<\/h2>/)
  assert.match(html, /<strong>138,422 元<\/strong>/)
  assert.match(html, /<table>/)
  assert.match(html, /<td style="text-align:right">56,500<\/td>/)
  assert.match(html, /data-copy="table"/)
})

test('段落、嵌套列表、引用、链接和强调各自保留语义', () => {
  const html = richText('第一段。\n接着说明。\n\n第二段。\n\n1. 查询\n   - 合同\n   - 回款\n2. 核对\n\n> 以业务记录为准\n\n[官方资料](https://example.com/docs?a=1&b=2) 与 *说明*、~~旧值~~。')
  assert.match(html, /<p>第一段。\n接着说明。<\/p>/)
  assert.match(html, /<ol>[\s\S]*<ul>[\s\S]*<\/ul>[\s\S]*<\/ol>/)
  assert.match(html, /<blockquote>/)
  assert.match(html, /href="https:\/\/example.com\/docs\?a=1&amp;b=2" target="_blank" rel="noopener noreferrer"/)
  assert.match(html, /<em>说明<\/em>/)
  assert.match(html, /<s>旧值<\/s>/)
})

test('代码中的 HTML、符号和空白不被当成正文或链接', () => {
  const html = richText('```javascript\nconst html = "<img onerror=alert(1)>"\n  const url = "https://example.com"\n```')
  assert.match(html, /data-copy="code"/)
  assert.match(html, /hljs-keyword/)
  assert.match(html, /&lt;img onerror=alert\(1\)&gt;/)
  assert.doesNotMatch(html, /<img|<a href=/)
  assert.match(html, /\n  /)
})

test('模型回复不能注入 HTML、事件、脚本或危险链接', () => {
  const inputs = [
    '<script>alert(1)</script><img src=x onerror=alert(1)>',
    '[点击](javascript:alert(1))',
    '[点击](data:text/html;base64,PHNjcmlwdD4=)',
    '[点击](vbscript:msgbox(1))',
    '```\" onclick=\"alert(1)\n</code><script>alert(1)</script>\n```',
    '![图片](javascript:alert(1))',
  ]
  for (const input of inputs) {
    const html = richText(input)
    assert.doesNotMatch(html, /<script|<img|href="(?:javascript|data|vbscript):/i)
    assert.doesNotMatch(html, /<[^>]*\sonclick=/i)
  }
  assert.doesNotMatch(richText('![远程图片](https://example.com/tracker.png)'), /<img/)
})

test('尚未结束的流式代码和表格可以安全渲染', () => {
  assert.match(richText('```python\nprint("进行中")'), /<pre[^>]*><code/)
  assert.match(richText('| 项目 | 金额 |\n| --- | ---: |\n| A |'), /<table>/)
  assert.equal(richText(null), '')
})
