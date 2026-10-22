// Real browser smoke checks. No request interception, fixtures, or credentials.
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import assert from 'node:assert/strict'
const root = fileURLToPath(new URL('../', import.meta.url))
const port = Number(process.env.CINELAB_SMOKE_PORT || 4179)
const origin = `http://127.0.0.1:${port}`
const server = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] })
let browser
try {
  let ready = false
  for (let n = 0; n < 50; n++) {
    try { if ((await fetch(origin)).ok) { ready = true; break } } catch {}
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  assert.ok(ready, 'Preview server did not start')
  const windowsChrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
  browser = await chromium.launch({ headless: true, ...(existsSync(windowsChrome) ? { executablePath: windowsChrome } : {}) })
  const results = []
  for (const [width, height] of [[1440, 1000], [390, 844], [320, 760]]) {
    const context = await browser.newContext({ viewport: { width, height } })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    for (const route of ['/', '/movies', '/experiments', '/experiments?user=014', '/experiments?source=ml&algorithm=realtime', '/login']) {
      await page.goto(origin + route, { waitUntil: 'networkidle' })
      await page.waitForSelector('main h1')
      const sizing = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth, title: document.querySelector('main h1')?.textContent }))
      assert.ok(sizing.content <= sizing.viewport + 1, `${width}px ${route} overflows: ${JSON.stringify(sizing)}`)
      if (route.includes('algorithm=realtime')) await page.getByRole('heading', { name: '历史用户不能产生实时行为' }).waitFor()
      if (route === '/experiments?user=014') {
        assert.equal(await page.locator('#history-id').inputValue(), '014')
        assert.equal(await page.locator('.subject-summary strong').textContent(), '尚未选择')
        await page.getByText('请输入原始正整数 ID（不含 ml:、空格或前导零）').waitFor()
      }
      if (route === '/experiments') {
        await page.locator('#history-id').fill('014')
        await page.getByRole('button', { name: '切换', exact: true }).click()
        await page.getByText('请输入原始正整数 ID（不含 ml:、空格或前导零）').waitFor()
      }
      results.push({ width, route, ...sizing })
    }
    assert.deepEqual(errors, [], 'Uncaught browser exceptions')
    await context.close()
  }
  const outdir = path.join(root, 'test-results')
  mkdirSync(outdir, { recursive: true })
  const report = { mode: 'actual-browser-no-interception', backend: 'not-required; unavailable state is retained', results }
  writeFileSync(path.join(outdir, 'layout-smoke.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2))
} finally {
  await browser?.close()
  server.kill()
}
