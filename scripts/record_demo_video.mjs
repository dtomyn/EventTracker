// Records the README feature-tour video against a running EventTracker server.
//
// The script drives headless Chromium through the main features, creates one
// real entry from a reference URL using AI generation, ends on the Poster
// Board, and then post-processes the recording with ffmpeg: the long AI wait
// is cut, a "~N seconds later" label marks the cut, and the result is encoded
// as an H.264 MP4 suitable for uploading as a GitHub README attachment.
//
// Usage (server must already be running, AI provider configured):
//   npm run demo:video
//   npm run demo:video -- --source-url https://example.com/post --out demo-output/tour.mp4
//
// Run `npm run demo:video -- --help` for all options.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { parseArgs } from 'node:util';

import { chromium } from '@playwright/test';

const DEFAULT_SOURCE_URL =
  'https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/';
const VIEWPORT = { width: 1440, height: 900 };
// Seconds of the generation spinner kept on each side of the cut.
const KEEP_BEFORE_CUT_SECONDS = 4;
const KEEP_AFTER_CUT_SECONDS = 1.5;
// Only cut the wait when it is long enough to be worth hiding.
const MIN_WAIT_TO_CUT_SECONDS = 10;
const CUT_LABEL_SECONDS = 2.5;
const LABEL_FONT_CANDIDATES = [
  'C:/Windows/Fonts/segoeuib.ttf',
  '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
  '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
];

const HELP = `Record the EventTracker feature-tour video.

Options:
  --base-url <url>      Running EventTracker server (default http://127.0.0.1:35231)
  --group-id <id>       Timeline group to tour (default 1)
  --source-url <url>    Reference URL used to create the new entry (default: Gemini 4 Argon post)
  --search <text>       Full-text search term to demonstrate (default Copilot)
  --out <file>          Output MP4 path (default demo-output/eventtracker-demo.mp4)
  --generate-timeout <seconds>
                        Maximum wait for AI generation (default 300)
  --keep-raw            Keep the raw WebM recording next to the MP4
  --headed              Show the browser while recording
  --help                Show this help

Notes:
  The run saves a real entry in the server's database. Back it up first if needed.
  ffmpeg is resolved from FFMPEG_PATH, then from the imageio-ffmpeg package via uv.`;

const { values: args } = parseArgs({
  options: {
    'base-url': { type: 'string', default: 'http://127.0.0.1:35231' },
    'group-id': { type: 'string', default: '1' },
    'source-url': { type: 'string', default: DEFAULT_SOURCE_URL },
    search: { type: 'string', default: 'Copilot' },
    out: { type: 'string', default: path.join('demo-output', 'eventtracker-demo.mp4') },
    'generate-timeout': { type: 'string', default: '300' },
    'keep-raw': { type: 'boolean', default: false },
    headed: { type: 'boolean', default: false },
    help: { type: 'boolean', default: false },
  },
});

if (args.help) {
  console.log(HELP);
  process.exit(0);
}

const baseUrl = args['base-url'].replace(/\/+$/, '');
const groupId = args['group-id'];
const sourceUrl = args['source-url'];
const outPath = path.resolve(args.out);
const generateTimeoutMs = Number(args['generate-timeout']) * 1000;

/** Visible cursor dot and caption banner, injected into every page. */
const OVERLAY_SCRIPT = `
(() => {
  const install = () => {
    if (document.getElementById('__demo_cursor')) return;
    const cursor = document.createElement('div');
    cursor.id = '__demo_cursor';
    cursor.style.cssText = 'position:fixed;z-index:2147483647;width:22px;height:22px;border-radius:50%;background:rgba(255,80,40,.55);border:2px solid #fff;box-shadow:0 0 6px rgba(0,0,0,.5);pointer-events:none;transform:translate(-50%,-50%);left:-50px;top:-50px;transition:width .1s,height .1s';
    document.body.appendChild(cursor);
    const caption = document.createElement('div');
    caption.id = '__demo_caption';
    caption.style.cssText = 'position:fixed;z-index:2147483646;left:50%;bottom:28px;transform:translateX(-50%);background:rgba(20,24,33,.88);color:#fff;font:600 20px Inter,Segoe UI,sans-serif;padding:10px 22px;border-radius:12px;pointer-events:none;opacity:0;transition:opacity .3s;white-space:nowrap';
    document.body.appendChild(caption);
    const saved = sessionStorage.getItem('__demo_caption');
    if (saved) { caption.textContent = saved; caption.style.opacity = 1; }
    document.addEventListener('mousemove', e => { cursor.style.left = e.clientX + 'px'; cursor.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', () => { cursor.style.width = cursor.style.height = '30px'; }, true);
    document.addEventListener('mouseup', () => { cursor.style.width = cursor.style.height = '22px'; }, true);
  };
  if (document.body) install(); else document.addEventListener('DOMContentLoaded', install);
})();`;

async function assertServerReady() {
  let response;
  try {
    response = await fetch(`${baseUrl}/?group_id=${encodeURIComponent(groupId)}`);
  } catch (error) {
    throw new Error(`EventTracker is not reachable at ${baseUrl}. Start it with: uv run python -m scripts.run_dev`, { cause: error });
  }
  if (!response.ok) {
    throw new Error(`EventTracker returned HTTP ${response.status} for group ${groupId}.`);
  }
}

/** The app rejects a second entry with the same source URL in a group, so fail before recording. */
async function assertSourceUrlIsNew() {
  const response = await fetch(`${baseUrl}/entries/export`);
  if (!response.ok) {
    throw new Error(`Could not check for existing entries (HTTP ${response.status}).`);
  }
  const payload = await response.json();
  const existing = payload.entries.find(
    entry => String(entry.group_id) === groupId && entry.source_url === sourceUrl,
  );
  if (existing) {
    throw new Error(
      `Entry ${existing.id} in group ${groupId} already uses ${sourceUrl}. Delete it or pick another --source-url.`,
    );
  }
}

function resolveFfmpeg() {
  if (process.env.FFMPEG_PATH) return process.env.FFMPEG_PATH;
  const result = spawnSync(
    'uv',
    ['run', '--no-project', '--with', 'imageio-ffmpeg', 'python', '-c', 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'],
    { encoding: 'utf8' },
  );
  const ffmpegPath = result.stdout?.trim().split(/\r?\n/).pop();
  if (result.status !== 0 || !ffmpegPath) {
    throw new Error(`Could not locate ffmpeg. Set FFMPEG_PATH or install uv.\n${result.stderr ?? ''}`);
  }
  return ffmpegPath;
}

/** Escapes a path for use inside an ffmpeg filtergraph option value. */
function filterPath(value) {
  return value.replace(/\\/g, '/').replace(/:/g, '\\:').replace(/'/g, "\\'");
}

function buildFilterGraph(cut) {
  if (!cut) {
    return '[0:v]null[v]';
  }
  const font = LABEL_FONT_CANDIDATES.find(candidate => existsSync(candidate));
  const label = font
    ? `,drawtext=fontfile='${filterPath(font)}':text='~${cut.skippedSeconds} seconds later':fontsize=22:fontcolor=white:box=1:boxcolor=0x141821@0.85:boxborderw=12:x=w-tw-60:y=110:enable='lt(t,${CUT_LABEL_SECONDS})'`
    : '';
  return [
    `[0:v]trim=0:${cut.start.toFixed(2)},setpts=PTS-STARTPTS[a]`,
    `[0:v]trim=start=${cut.end.toFixed(2)},setpts=PTS-STARTPTS${label}[b]`,
    '[a][b]concat=n=2:v=1[v]',
  ].join(';');
}

function encodeVideo(ffmpeg, rawPath, cut) {
  const result = spawnSync(
    ffmpeg,
    [
      '-hide_banner', '-loglevel', 'error', '-y',
      '-i', rawPath,
      '-filter_complex', buildFilterGraph(cut),
      '-map', '[v]',
      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', '-preset', 'slow',
      '-movflags', '+faststart',
      outPath,
    ],
    { encoding: 'utf8' },
  );
  if (result.status !== 0) {
    throw new Error(`ffmpeg failed:\n${result.stderr}`);
  }
}

async function recordTour(videoDir) {
  const browser = await chromium.launch({ headless: !args.headed });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    recordVideo: { dir: videoDir, size: VIEWPORT },
  });
  await context.addInitScript(OVERLAY_SCRIPT);
  const page = await context.newPage();
  // The video starts when the page is created; generation timestamps are relative to this.
  const recordingStart = Date.now();
  page.setDefaultTimeout(30000);

  const pause = ms => page.waitForTimeout(ms);
  const caption = text => page.evaluate(value => {
    sessionStorage.setItem('__demo_caption', value);
    const banner = document.getElementById('__demo_caption');
    if (banner) { banner.textContent = value; banner.style.opacity = 1; }
  }, text);
  const move = async locator => {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 });
    await pause(250);
  };
  const click = async locator => { await move(locator); await locator.click(); };
  const type = async (locator, text, delay) => {
    await click(locator);
    await locator.fill('');
    await locator.pressSequentially(text, { delay });
  };
  const scroll = async (deltaY, steps = 10) => {
    for (let i = 0; i < steps; i++) {
      await page.mouse.wheel(0, deltaY / steps);
      await pause(60);
    }
  };

  let cut = null;
  let savedUrl;
  try {
    // 1. Timeline
    await page.goto(`${baseUrl}/?group_id=${encodeURIComponent(groupId)}`);
    await page.mouse.move(VIEWPORT.width / 2, VIEWPORT.height / 2);
    await caption('Timeline - your events grouped by month');
    await pause(2000);
    await scroll(900);
    await pause(1200);
    await scroll(-900);
    await pause(600);

    // 2. Timeline views
    const views = page.getByRole('group', { name: 'Timeline views' });
    for (const name of ['Summaries', 'Months', 'Years', 'Heatmap']) {
      await caption(`${name} view`);
      await click(views.getByRole('button', { name, exact: true }));
      await pause(2200);
    }
    await click(views.getByRole('button', { name: 'Details', exact: true }));
    await pause(1200);

    // 3. Search
    await caption('Full-text search across entries');
    await type(page.locator('input[name="q"]').first(), args.search, 80);
    await pause(400);
    await click(page.getByRole('button', { name: 'Search', exact: true }).first());
    await page.waitForLoadState('networkidle');
    await pause(2500);
    await scroll(500);
    await pause(1000);

    // 4. New entry from a reference URL
    await caption('Add a new entry from a reference URL');
    await click(page.getByRole('link', { name: 'New Entry' }).first());
    await page.waitForLoadState('networkidle');
    await pause(1000);
    await type(page.getByLabel('Source URL'), sourceUrl, 15);
    await pause(600);
    await caption('AI drafts the summary, title, date and tags');
    await click(page.locator('#generate-button'));
    const generateStart = (Date.now() - recordingStart) / 1000;
    // Resolves when the form is filled, or when the button returns to idle after a failure.
    const formFilled = () => page.evaluate(
      () => document.getElementById('final_text')?.value.trim().length > 0
        && document.getElementById('title')?.value.trim().length > 0,
    );
    await page.waitForFunction(
      () => (document.getElementById('final_text')?.value.trim().length > 0
        && document.getElementById('title')?.value.trim().length > 0)
        || !document.getElementById('generate-button-label')?.textContent.includes('Generating'),
      null,
      { timeout: generateTimeoutMs },
    ).catch(() => {});
    if (!(await formFilled())) {
      const feedback = await page.locator('#generate-feedback').innerText().catch(() => '');
      throw new Error(`AI generation did not fill the form. ${feedback}`.trim());
    }
    const generateEnd = (Date.now() - recordingStart) / 1000;
    const waitSeconds = generateEnd - generateStart;
    console.log(`AI generation took ${waitSeconds.toFixed(1)}s.`);
    if (waitSeconds >= MIN_WAIT_TO_CUT_SECONDS) {
      const start = generateStart + KEEP_BEFORE_CUT_SECONDS;
      const end = generateEnd - KEEP_AFTER_CUT_SECONDS;
      cut = { start, end, skippedSeconds: Math.round((end - start) / 10) * 10 };
    }
    const newTitle = await page.getByLabel('Title').inputValue();
    await pause(1500);
    await move(page.getByLabel('Title'));
    await pause(800);
    await move(page.getByLabel('Tags'));
    await pause(800);
    await scroll(400);
    await pause(1500);

    await caption('Save the entry');
    const [saveResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/entries/new', { timeout: 60000 }),
      click(page.getByRole('button', { name: 'Save Entry' })),
    ]);
    if (saveResponse.status() >= 400) {
      await page.waitForLoadState('load');
      const errors = await page.locator('.invalid-feedback, .alert-danger').allInnerTexts();
      throw new Error(`Saving the entry failed (HTTP ${saveResponse.status()}): ${errors.join('; ') || 'no validation message shown'}`);
    }
    await page.waitForURL(/\/entries\/\d+\/view$/, { timeout: 60000 });
    savedUrl = page.url();
    await pause(2500);
    await scroll(500);
    await pause(1500);

    // 5. Back to the timeline to show the new entry
    await caption('The new entry appears on the timeline');
    await page.goto(`${baseUrl}/?group_id=${encodeURIComponent(groupId)}`);
    await page.mouse.move(VIEWPORT.width / 2, VIEWPORT.height / 2);
    await pause(1000);
    const newCard = page.getByText(newTitle).first();
    if (await newCard.isVisible()) await move(newCard);
    await pause(2500);
    await scroll(-2000);
    await pause(600);

    // 6. Poster Board
    await caption('Poster Board');
    await click(page.locator('[data-poster-board-link]'));
    await page.waitForLoadState('networkidle');
    await page.mouse.move(VIEWPORT.width / 2, VIEWPORT.height / 2, { steps: 20 });
    await pause(4000);
  } finally {
    await context.close();
    await browser.close();
  }

  return { rawPath: await page.video().path(), cut, savedUrl };
}

async function main() {
  await assertServerReady();
  await assertSourceUrlIsNew();
  // Resolve ffmpeg up front so a missing encoder fails before an entry is saved.
  const ffmpeg = resolveFfmpeg();
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  const videoDir = await fs.mkdtemp(path.join(os.tmpdir(), 'eventtracker-demo-'));
  try {
    const { rawPath, cut, savedUrl } = await recordTour(videoDir);
    console.log(`Saved new entry: ${savedUrl}`);
    if (args['keep-raw']) {
      await fs.copyFile(rawPath, outPath.replace(/\.mp4$/i, '') + '.raw.webm');
    }
    encodeVideo(ffmpeg, rawPath, cut);
    console.log(`Video written to ${outPath}`);
  } finally {
    await fs.rm(videoDir, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(error.message);
  // Avoid process.exit(): on Windows it can abort while fetch sockets are closing.
  process.exitCode = 1;
});
