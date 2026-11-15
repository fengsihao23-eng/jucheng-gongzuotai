import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
function files(dir) {
  if (!existsSync(path.join(root, dir))) return []
  return readdirSync(path.join(root, dir), { withFileTypes: true }).flatMap(entry => {
    const name = path.join(dir, entry.name)
    return entry.isDirectory() ? files(name) : entry.isFile() ? [name] : []
  })
}
export function buildInfo() {
  const hash = createHash('sha256')
  const inputs = [...files('src'), ...files('public'), ...files('scripts'), 'index.html', 'vite.config.js', 'package.json', 'package-lock.json'].sort()
  for (const name of inputs) { hash.update(name + '\0'); hash.update(readFileSync(path.join(root, name))); hash.update('\0') }
  return { revision: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    source_sha256: hash.digest('hex') }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) console.log(JSON.stringify(buildInfo()))
