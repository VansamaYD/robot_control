// Dedicated read-only renderer. stdout contains only one machine-readable result.
import { chromium } from 'playwright';
import fs from 'node:fs';
import crypto from 'node:crypto';

let browser;
try {
  const [target, widthArg, heightArg] = process.argv.slice(2);
  const url = new URL(target);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) throw Error('INVALID_RENDER_URL');
  const width = Number(widthArg), height = Number(heightArg);
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 320 || width > 1920 || height < 240 || height > 1080) throw Error('INVALID_IMAGE_SIZE');
  const systemChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  browser = await chromium.launch({ headless: true, chromiumSandbox: true,
    executablePath: process.env.CHROME_PATH || (fs.existsSync(systemChrome) ? systemChrome : undefined),
    args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(target);
  await page.waitForFunction(() => window.__ready, null, { timeout: 20000 });
  const state = await page.evaluate(() => window.__frameState);
  if (errors.length || state.tcp_error_m > 1e-8) throw Error('RENDER_STATE_MISMATCH');
  const png = await page.screenshot();
  process.stdout.write(JSON.stringify({ mimeType: 'image/png', data: png.toString('base64'),
    metadata: { session_id: state.session_id, incarnation: state.incarnation,
      state_revision: state.state_revision, tick: state.tick, simulation_time_s: state.simulation_time_s,
      configuration_revision: state.configuration_revision, source: 'simulation',
      q_rad: state.q_rad, liquid: state.liquid, camera: state.camera, width, height,
      image_sha256: crypto.createHash('sha256').update(png).digest('hex') } }));
} catch (error) {
  console.error(String(error)); process.exitCode = 1;
} finally { if (browser) await browser.close(); }
