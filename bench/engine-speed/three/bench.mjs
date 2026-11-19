import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer';

const here = path.dirname(fileURLToPath(import.meta.url));
const resultsDir = path.resolve(here, '../results');
const frames = Number(process.env.BENCH_FRAMES || 72);
const fps = Number(process.env.BENCH_FPS || 24);
const width = 1280;
const height = 720;

function inside(file, dir) {
  const rel = path.relative(dir, file);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

function startServer() {
  const publicDir = path.join(here, 'public');
  const threeDir = path.join(here, 'node_modules', 'three');
  const server = http.createServer((req, res) => {
    try {
      const url = new URL(req.url || '/', 'http://127.0.0.1');
      const rel = decodeURIComponent(url.pathname);
      if (rel.includes('\0')) {
        res.writeHead(400);
        res.end('bad path');
        return;
      }
      const file = rel.startsWith('/vendor/three/')
        ? path.join(threeDir, rel.slice('/vendor/three/'.length))
        : path.join(publicDir, rel === '/' ? 'index.html' : rel);
      const resolved = path.resolve(file);
      const root = rel.startsWith('/vendor/three/') ? threeDir : publicDir;
      if (!inside(resolved, root) || !fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
        res.writeHead(404);
        res.end('not found');
        return;
      }
      const ext = path.extname(resolved);
      const types = {
        '.html': 'text/html; charset=utf-8',
        '.js': 'text/javascript; charset=utf-8',
      };
      res.writeHead(200, { 'content-type': types[ext] || 'application/octet-stream' });
      res.end(fs.readFileSync(resolved));
    } catch (err) {
      res.writeHead(500);
      res.end(err instanceof Error ? err.message : String(err));
    }
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

const attempts = [
  {
    method: 'swiftshader',
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  },
  {
    method: 'angle-gl',
    args: ['--use-gl=angle', '--use-angle=gl'],
  },
  {
    method: 'default',
    args: [],
  },
];

const commonArgs = [
  '--no-sandbox',
  '--disable-setuid-sandbox',
  '--disable-dev-shm-usage',
  '--disable-gpu-sandbox',
  '--ignore-gpu-blocklist',
  '--enable-webgl',
  '--window-size=1280,720',
  '--hide-scrollbars',
  '--mute-audio',
];

function writeJson(name, data) {
  fs.mkdirSync(resultsDir, { recursive: true });
  fs.writeFileSync(path.join(resultsDir, name), `${JSON.stringify(data, null, 2)}\n`);
}

async function openPage(browser, port) {
  const page = await browser.newPage();
  page.setDefaultTimeout(600000);
  page.setDefaultNavigationTimeout(120000);
  page.on('console', (msg) => {
    console.log(`PAGE ${msg.type()}: ${msg.text()}`);
  });
  page.on('pageerror', (err) => {
    console.log(`PAGEERROR ${err.message}`);
  });
  await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'load' });
  await page.waitForFunction(() => typeof window.runBench === 'function', { timeout: 30000 });
  return page;
}

async function measure(page, readback) {
  const started = performance.now();
  const result = await page.evaluate((opts) => window.runBench(opts), {
    width,
    height,
    frames,
    fps,
    readback,
  });
  const wallS = (performance.now() - started) / 1000;
  return { ...result, wall_s: wallS, exit_code: 0 };
}

async function tryLaunch(method, extraArgs) {
  const browser = await puppeteer.launch({
    headless: false,
    protocolTimeout: 600000,
    timeout: 120000,
    defaultViewport: { width, height, deviceScaleFactor: 1 },
    args: [...commonArgs, ...extraArgs],
  });
  return browser;
}

async function main() {
  if (!Number.isInteger(frames) || frames < 1) {
    throw new Error('BENCH_FRAMES must be a positive integer');
  }
  const server = await startServer();
  const port = server.address().port;
  const failures = [];
  let chosen = null;

  try {
    for (const attempt of attempts) {
      let browser;
      try {
        console.log(`trying three.js via ${attempt.method}`);
        const launched = performance.now();
        browser = await tryLaunch(attempt.method, attempt.args);
        const version = await browser.version();
        const page = await openPage(browser, port);
        const opened = performance.now();
        const probe = await page.evaluate((opts) => window.runBench(opts), {
          width,
          height,
          frames: 4,
          fps,
          readback: false,
        });
        console.log(`probe ${attempt.method}: ${probe.adapter} coverage=${probe.pixel_coverage} ${probe.width}x${probe.height}`);
        if (!probe.pixel_ok) {
          failures.push({
            method: attempt.method,
            adapter: probe.adapter,
            error: `画面覆盖 ${Number(probe.pixel_coverage).toFixed(3)}，分辨率 ${probe.width}x${probe.height}`,
          });
          await browser.close();
          continue;
        }
        chosen = {
          browser,
          page,
          method: attempt.method,
          version,
          probe,
          startupS: (opened - launched) / 1000 + (performance.now() - opened) / 1000,
        };
        break;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.log(`attempt ${attempt.method} failed: ${message}`);
        failures.push({ method: attempt.method, error: message });
        if (browser) await browser.close().catch(() => {});
      }
    }

    if (!chosen) {
      writeJson('three-error.json', {
        engine: 'three',
        pixel_ok: false,
        error: 'Chrome 没有画出三维画面',
        attempts: failures,
      });
      process.exitCode = 1;
      return;
    }

    for (const readback of [false, true]) {
      const mode = readback ? 'readback' : 'render';
      console.log(`measuring three.js ${mode}`);
      const row = await measure(chosen.page, readback);
      row.method = chosen.method;
      row.chrome = chosen.version;
      row.startup_s = chosen.startupS + Math.max(0, row.wall_s - row.loop_s);
      row.probe_adapter = chosen.probe.adapter;
      writeJson(`three-${mode}.json`, row);
      console.log(`${mode}: ${row.loop_fps.toFixed(2)} fps, coverage ${row.pixel_coverage.toFixed(3)}`);
    }
  } finally {
    if (chosen) await chosen.browser.close().catch(() => {});
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((err) => {
  console.error(err);
  writeJson('three-error.json', {
    engine: 'three',
    pixel_ok: false,
    error: err instanceof Error ? err.stack || err.message : String(err),
  });
  process.exitCode = 1;
});
