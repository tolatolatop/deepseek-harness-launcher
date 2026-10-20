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
  if (errors.length || failures.length) throw new Error(JSON.stringify({ errors, failures }))
  const body = await page.locator('body').innerText()
  if (body.length < 50) throw new Error('Harness UI did not mount')
  mkdirSync('docs/screenshots', { recursive: true })
  await page.screenshot({ path: 'docs/screenshots/harness.png', fullPage: true })
  console.log('PASS: native Harness browser UI and authenticated assets')
} finally {
  await browser.close()
}
