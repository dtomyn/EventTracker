// Connection Graph and Tag Clusters pages:
// 1. Clicking a node opens the side panel with entry details; Esc closes it and restores focus.
// 2. User text is rendered as text, never as HTML.
// 3. Time-lapse scrubber reveals entries chronologically and links only when both ends show.
// 4. Play animates the time-lapse to the latest entry.
// 5. Reduced motion disables playback but the scrubber still works.
// 6. Tag Clusters panel lists related tags and entries and can jump between tags.

import type { Page } from '@playwright/test';

import { expect, test } from './helpers/harness.js';
import { GraphPage } from './poms/graph-page.js';

const D3_CDN_URL = 'https://cdn.jsdelivr.net/npm/d3@7';
let d3ScriptBody: string | null = null;

/** Fetch D3 once per worker so the graph engine can run behind the harness CDN block. */
async function fetchD3Script(): Promise<string> {
  if (d3ScriptBody !== null) {
    return d3ScriptBody;
  }
  try {
    const response = await fetch(D3_CDN_URL, { redirect: 'follow', signal: AbortSignal.timeout(30_000) });
    d3ScriptBody = response.ok ? await response.text() : '';
  } catch {
    d3ScriptBody = '';
  }
  return d3ScriptBody;
}

async function allowD3Script(page: Page): Promise<void> {
  const body = await fetchD3Script();
  test.skip(body.length === 0, 'D3 could not be downloaded from the CDN.');
  await page.route('**/npm/d3@7**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/javascript', body });
  });
}

const XSS_TITLE = '<img src=x onerror="window.__graphXss=1">Launch';

const CONNECTIONS = {
  nodes: [
    {
      id: 101, label: 'Alpha launch', size: 1, display_date: 'January 5, 2026', date: '2026-01-05',
      sort_key: 20260105, tags: ['release', 'ai'], excerpt: 'Alpha shipped to everyone.', group_name: 'Graph Group',
    },
    {
      id: 102, label: XSS_TITLE, size: 2, display_date: 'February 10, 2026', date: '2026-02-10',
      sort_key: 20260210, tags: [], excerpt: '<b>not bold</b>', group_name: 'Graph Group',
    },
    {
      id: 103, label: 'Gamma follow-up', size: 1, display_date: 'March 20, 2026', date: '2026-03-20',
      sort_key: 20260320, tags: ['ai'], excerpt: 'Gamma builds on beta.', group_name: 'Graph Group',
    },
  ],
  edges: [
    { source: 101, target: 102, weight: 1, note: 'Alpha leads to beta', type: 'explicit' },
    { source: 102, target: 101, weight: 1, note: '', type: 'explicit' },
    { source: 102, target: 103, weight: 1, note: '', type: 'explicit' },
  ],
};

const TOPICS = {
  nodes: [
    { id: 'ai', label: 'ai', entry_ids: [101, 103], size: 2 },
    { id: 'release', label: 'release', entry_ids: [101], size: 1 },
    { id: 'safety', label: 'safety', entry_ids: [103], size: 1 },
  ],
  edges: [
    { source: 'ai', target: 'release', weight: 1 },
    { source: 'ai', target: 'safety', weight: 1 },
  ],
  entries: {
    '101': { id: 101, title: 'Alpha launch', display_date: 'Jan 5, 2026', sort_key: 20260105 },
    '103': { id: 103, title: 'Gamma follow-up', display_date: 'Mar 20, 2026', sort_key: 20260320 },
  },
};

async function mockApi(page: Page, groupId: number): Promise<void> {
  await page.route(`**/api/groups/${groupId}/connections**`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONNECTIONS) }),
  );
  await page.route(`**/api/groups/${groupId}/topics`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TOPICS) }),
  );
}

async function openConnectionGraph(page: Page, groupId: number): Promise<GraphPage> {
  await allowD3Script(page);
  await mockApi(page, groupId);
  const graph = new GraphPage(page);
  await graph.gotoConnections(groupId);
  await graph.waitForReady();
  return graph;
}

test('connection graph side panel shows entry details and closes with Escape', async ({
  ensureDedicatedGroup,
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  const groupId = await ensureDedicatedGroup();
  const graph = await openConnectionGraph(page, groupId);

  await expect(graph.nodes).toHaveCount(3);
  // Reciprocal connections render as a single link.
  await expect(graph.links).toHaveCount(2);
  await expect(graph.legend).toBeVisible();
  await expect(graph.fit).toBeEnabled();
  await expect(graph.panel).toHaveAttribute('aria-hidden', 'true');

  await graph.node(101).click();
  await expect(graph.panel).toHaveClass(/is-open/);
  await expect(graph.panelTitle).toHaveText('Alpha launch');
  await expect(graph.panel).toContainText('January 5, 2026');
  await expect(graph.panel).toContainText('Alpha shipped to everyone.');
  await expect(graph.panel.locator('.eg-chip')).toHaveText(['release', 'ai']);
  await expect(graph.panel.getByRole('link', { name: 'Open entry' })).toHaveAttribute('href', '/entries/101/view');
  await expect(graph.panel).toContainText('Alpha leads to beta');
  await expect(graph.panelClose).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(graph.panel).not.toHaveClass(/is-open/);
  await expect(graph.node(101)).toBeFocused();

  // Keyboard: Enter on a focused node opens the panel; the close button dismisses it.
  await graph.node(102).focus();
  await page.keyboard.press('Enter');
  await expect(graph.panelTitle).toHaveText(XSS_TITLE);
  await expect(graph.panel).toContainText('<b>not bold</b>');
  await expect(graph.panel.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __graphXss?: number }).__graphXss)).toBeUndefined();

  // Jump to a connected entry from the panel list.
  await graph.panel.getByRole('button', { name: 'Show Gamma follow-up in graph' }).click();
  await expect(graph.panelTitle).toHaveText('Gamma follow-up');
  await graph.panelClose.click();
  await expect(graph.panel).not.toHaveClass(/is-open/);

  expect(errors).toEqual([]);
});

test('connection graph time-lapse scrubber reveals entries chronologically', async ({
  ensureDedicatedGroup,
  page,
}) => {
  const groupId = await ensureDedicatedGroup();
  const graph = await openConnectionGraph(page, groupId);

  await expect(graph.scrubber).toBeVisible();
  await expect(graph.timelapseCount).toHaveText('3 of 3 entries');

  await graph.scrubTo(0);
  await expect(graph.timelapseCount).toHaveText('1 of 3 entries');
  await expect(graph.visibleNodes).toHaveCount(1);
  await expect(graph.hiddenLinks).toHaveCount(2);
  await expect(graph.timelapseDate).toContainText('2026');

  // Feb 10 is 36 days after Jan 5: both early entries and their link appear.
  await graph.scrubTo(36);
  await expect(graph.timelapseCount).toHaveText('2 of 3 entries');
  await expect(graph.hiddenLinks).toHaveCount(1);

  await graph.scrubTo(0);
  await graph.playButton.click();
  await expect(graph.playButton).toHaveAttribute('aria-label', 'Pause time-lapse');
  await expect(graph.timelapseCount).toHaveText('3 of 3 entries', { timeout: 20_000 });
  await expect(graph.playButton).toHaveAttribute('aria-label', 'Play time-lapse');
  await expect(graph.hiddenLinks).toHaveCount(0);
});

test('connection graph respects reduced motion', async ({ ensureDedicatedGroup, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const groupId = await ensureDedicatedGroup();
  const graph = await openConnectionGraph(page, groupId);

  await expect(graph.playButton).toBeDisabled();
  await graph.scrubTo(0);
  await expect(graph.timelapseCount).toHaveText('1 of 3 entries');
});

test('tag clusters side panel lists related tags and entries', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  await allowD3Script(page);
  await mockApi(page, groupId);
  const graph = new GraphPage(page);
  await graph.gotoTopics(groupId);
  await graph.waitForReady();

  await expect(graph.nodes).toHaveCount(3);
  await expect(graph.scrubber).toHaveCount(0);

  await graph.node('ai').click();
  await expect(graph.panelTitle).toHaveText('ai');
  await expect(graph.panel).toContainText('2 entries');
  const entryLinks = graph.panel.locator('a.eg-list-item');
  await expect(entryLinks).toHaveCount(2);
  // Newest entry first.
  await expect(entryLinks.first()).toHaveAttribute('href', '/entries/103/view');
  await expect(graph.panel.getByRole('link', { name: 'Filter timeline by tag' })).toHaveAttribute(
    'href',
    `/?q=ai&group_id=${groupId}`,
  );

  await graph.panel.getByRole('button', { name: 'Show tag release in graph' }).click();
  await expect(graph.panelTitle).toHaveText('release');
  await graph.panelClose.click();
  await expect(graph.panel).not.toHaveClass(/is-open/);
});
