import { chromium } from '../web/node_modules/playwright/index.mjs'
import { readFileSync, mkdirSync } from 'node:fs'

const config = JSON.parse(readFileSync(0, 'utf8'))
const browser = await chromium.launch()
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addCookies([{
    name: 'launcher_session', value: config.session,
    domain: 'localhost', path: '/launcher', httpOnly: true, sameSite: 'Strict',
  }])
  const page = await context.newPage()
  const errors = []
  const failures = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('response', r => {
    if (r.url().startsWith(config.origin) && r.status() >= 400)
      failures.push({ path: new URL(r.url()).pathname, status: r.status() })
  })
  await page.goto(`${config.origin}/launcher/w/${config.workspace}/`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(4000)
  const previewNotice = page.getByRole('button', { name: 'Continue', exact: true })
  if (await previewNotice.isVisible()) {
    await previewNotice.click()
    await page.waitForTimeout(1000)
  }
  if (config.desktop) {
    let rfbConnected = false
    page.on('websocket', socket => {
      if (socket.url().includes('/desktop/websockify')) {
        socket.on('framereceived', event => {
          const payload = event.payload
          if ((typeof payload === 'string' ? payload : payload.toString()).startsWith('RFB ')) rfbConnected = true
        })
      }
    })
    await page.getByRole('button', { name: 'Open browser desktop for human takeover', exact: true }).click()
    const frame = page.frameLocator('iframe[title="Container Chromium desktop"]')
    try {
      await frame.locator('html.noVNC_connected').waitFor({ state: 'attached', timeout: 20000 })
    } catch (error) {
      console.error(JSON.stringify({ errors, failures, src: await page.locator('iframe[title="Container Chromium desktop"]').getAttribute('src'), frames: page.frames().map(f => ({url:f.url()})) }))
      for (const f of page.frames()) if (f.url().includes('/desktop/')) console.error((await f.locator('body').innerText().catch(() => '')).slice(-2000))
      await page.screenshot({path:'/tmp/launcher-desktop-failure.png',fullPage:true})
      throw error
    }
    await frame.locator('#noVNC_container canvas').waitFor({ state: 'visible', timeout: 10000 })
    if (!rfbConnected) throw new Error('noVNC did not receive an RFB handshake')
    const canvas = frame.locator('#noVNC_container canvas')
    await canvas.click()
    await page.keyboard.press('Control+l')
    await page.waitForTimeout(350)
    await page.keyboard.press('Control+a')
    await page.waitForTimeout(150)
    await page.keyboard.type('data:text/html,<title>Launcher browser desktop</title><h1>Browser desktop is ready</h1><p>DeepSeek Harness + Launcher</p>', {delay: 30})
    await page.keyboard.press('Enter')
    await page.waitForTimeout(1500)
    await frame.locator('#noVNC_transition').waitFor({state:'hidden',timeout:10000})
    const src = await page.locator('iframe[title="Container Chromium desktop"]').getAttribute('src')
    if (!src.startsWith(config.origin + '/launcher/w/' + config.workspace + '/desktop/')) throw new Error('Desktop iframe bypasses Launcher origin')
  }
  if (errors.length || failures.length) throw new Error(JSON.stringify({ errors, failures }))
  const body = await page.locator('body').innerText()
  if (body.length < 50) throw new Error('Harness UI did not mount')
  mkdirSync('docs/screenshots', { recursive: true })
  await page.screenshot({ path: config.desktop ? 'docs/screenshots/browser-desktop.png' : 'docs/screenshots/harness.png', fullPage: true })
  console.log('PASS: native Harness browser UI and authenticated assets')
} finally {
  await browser.close()
}
