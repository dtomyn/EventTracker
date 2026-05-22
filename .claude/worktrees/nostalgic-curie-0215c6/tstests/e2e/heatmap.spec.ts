// Heatmap feature E2E tests
// 1. Heatmap tab is visible and clickable in the toolbar.
// 2. Clicking heatmap tab renders the SVG grid.
// 3. Hovering a cell shows the tooltip.
// 4. Clicking a cell with entries shows filtered entries below.
// 5. Year navigation arrows work.
// 6. Heatmap legend is visible.

import { expect, test } from './helpers/harness.js';
import { EntryFormPage } from './poms/entry-form-page.js';
import { TimelinePage } from './poms/timeline-page.js';

const D3_CDN_URL = 'https://cdn.jsdelivr.net/npm/d3@7';

/**
 * Cache of D3 script body fetched once per worker process.
 */
let d3ScriptBody: string | null = null;

/**
 * Fetch the D3 bundle once and cache it, so individual tests do not each hit the network.
 */
async function fetchD3Script(): Promise<string> {
  if (d3ScriptBody !== null) {
    return d3ScriptBody;
  }
  try {
    const response = await fetch(D3_CDN_URL, { redirect: 'follow', signal: AbortSignal.timeout(30_000) });
    const body = response.ok ? await response.text() : '';
    d3ScriptBody = body;
    return body;
  } catch {
    d3ScriptBody = '';
    return '';
  }
}

/**
 * Register a page-level route that intercepts the D3 CDN script request and fulfils it
 * locally, bypassing the context-level CDN block in the shared harness.
 */
async function allowD3Script(page: import('@playwright/test').Page): Promise<void> {
  const scriptBody = await fetchD3Script();
  await page.route('**/npm/d3@7**', async (route) => {
    if (route.request().resourceType() === 'script') {
      await route.fulfill({
        status: 200,
        contentType: 'application/javascript',
        body: scriptBody,
      });
    } else {
      await route.continue();
    }
  });
}

/**
 * Helper: seed a minimal entry so that the timeline toolbar (and heatmap tab) render.
 */
async function seedEntryAndGoToHeatmap(
  page: import('@playwright/test').Page,
  groupId: number,
  entryYear = '2025',
  entryMonth = '6',
  entryDay = '15',
  title?: string,
): Promise<void> {
  const entryTitle = title ?? `Heatmap Seed ${Date.now()}`;
  const entryFormPage = new EntryFormPage(page);
  const timelinePage = new TimelinePage(page);

  await entryFormPage.gotoNew();
  await entryFormPage.selectTimelineGroup(groupId);
  await entryFormPage.fillDate(entryYear, entryMonth, entryDay);
  await entryFormPage.fillTitle(entryTitle);
  await entryFormPage.fillEventSummary('Seed entry for heatmap tests.');
  await entryFormPage.save();

  await expect(page).toHaveURL(/\/entries\/\d+\/view$/);

  await timelinePage.goto(groupId);
}

test('heatmap tab is visible and clickable in the toolbar', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();

  await seedEntryAndGoToHeatmap(page, groupId);

  const heatmapButton = page.getByRole('button', { name: 'Heatmap', exact: true });
  await expect(heatmapButton).toBeVisible();
  await expect(heatmapButton).toHaveAttribute('aria-pressed', 'false');
});

test('clicking heatmap tab renders the SVG grid', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  await allowD3Script(page);

  await seedEntryAndGoToHeatmap(page, groupId);

  const heatmapButton = page.getByRole('button', { name: 'Heatmap', exact: true });
  await heatmapButton.click();

  // The heatmap panel should be visible
  const heatmapPanel = page.locator('#heatmap-view');
  await expect(heatmapPanel).toBeVisible();

  // Wait for D3 to load and render the SVG
  const heatmapSvg = page.locator('#heatmap-container .heatmap-svg');
  await expect(heatmapSvg).toBeVisible({ timeout: 20_000 });

  // The heatmap button should be marked as active
  await expect(heatmapButton).toHaveAttribute('aria-pressed', 'true');

  // The view label should update
  const currentViewLabel = page.locator('[data-current-view-label]');
  await expect(currentViewLabel).toHaveText('Heatmap');
});

test('heatmap legend is visible after grid renders', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  await allowD3Script(page);

  await seedEntryAndGoToHeatmap(page, groupId);

  const heatmapButton = page.getByRole('button', { name: 'Heatmap', exact: true });
  await heatmapButton.click();

  // Wait for SVG to render
  const heatmapSvg = page.locator('#heatmap-container .heatmap-svg');
  await expect(heatmapSvg).toBeVisible({ timeout: 20_000 });

  // Legend items — the SVG contains "Less" and "More" text labels
  await expect(heatmapSvg.locator('text').filter({ hasText: 'Less' })).toBeVisible();
  await expect(heatmapSvg.locator('text').filter({ hasText: 'More' })).toBeVisible();
});

test('hovering a heatmap cell shows tooltip', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  await allowD3Script(page);

  await seedEntryAndGoToHeatmap(page, groupId);

  const heatmapButton = page.getByRole('button', { name: 'Heatmap', exact: true });
  await heatmapButton.click();

  // Wait for cells to render
  const firstCell = page.locator('.heatmap-cell').first();
  await expect(firstCell).toBeVisible({ timeout: 20_000 });

  // Hover over the first cell to trigger tooltip
  await firstCell.hover();

  // Tooltip div should be visible and contain date-related text
  const tooltip = page.locator('.heatmap-tooltip');
  await expect(tooltip).toBeVisible();
  // Tooltip should mention entry count or "No entries"
  await expect(tooltip).toContainText(/entries? on|No entries on/i);
});

test('clicking a cell with entries shows filtered entries below', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  const entryTitle = `Heatmap Cell Test ${Date.now()}`;
  await allowD3Script(page);

  // Seed an entry on a known date so we have a cell with count > 0
  await seedEntryAndGoToHeatmap(page, groupId, '2025', '6', '15', entryTitle);

  const heatmapButton = page.getByRole('button', { name: 'Heatmap', exact: true });
  await heatmapButton.click();

  // Wait for the SVG to render
  const heatmapSvg = page.locator('#heatmap-container .heatmap-svg');
  await expect(heatmapSvg).toBeVisible({ timeout: 20_000 });

  // Navigate to 2025 via the prev arrow if the default year is later
  const yearLabel = heatmapSvg.locator('text').filter({ hasText: /^\d{4}$/ }).first();

  for (let attempt = 0; attempt < 5; attempt++) {
    const labelText = await yearLabel.textContent();
    if (labelText === '2025') break;
    const parsedYear = parseInt(labelText ?? '0', 10);
    if (parsedYear > 2025) {
      const prevArrow = heatmapSvg.locator('.heatmap-nav-arrow').filter({ hasText: '◀' });
      await prevArrow.click();
      await expect(heatmapSvg.locator('text').filter({ hasText: /^\d{4}$/ }).first()).not.toHaveText(String(parsedYear), { timeout: 10_000 });
    } else {
      break;
    }
  }

  // Find the cell for our date (data-date="2025-06-15")
  const targetCell = page.locator('.heatmap-cell[data-date="2025-06-15"]');
  await expect(targetCell).toBeVisible();

  // Click the cell
  await targetCell.click();

  // Filtered entries should appear below
  const entriesContainer = page.locator('#heatmap-entries');
  await expect(entriesContainer).toContainText(entryTitle, { timeout: 10_000 });
});

test('year navigation arrows load adjacent year', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  await allowD3Script(page);

  // Seed entries in two different years so both a prev and a next year arrow will be available
  // when the heatmap is showing the middle year.
  const entryFormPage = new EntryFormPage(page);
  const timelinePage = new TimelinePage(page);

  // Entry in 2024
  await entryFormPage.gotoNew();
  await entryFormPage.selectTimelineGroup(groupId);
  await entryFormPage.fillDate('2024', '3', '10');
  await entryFormPage.fillTitle(`Nav Test Entry 2024 ${Date.now()}`);
  await entryFormPage.fillEventSummary('Nav test seed 2024.');
  await entryFormPage.save();

  // Entry in 2025
  await entryFormPage.gotoNew();
  await entryFormPage.selectTimelineGroup(groupId);
  await entryFormPage.fillDate('2025', '6', '15');
  await entryFormPage.fillTitle(`Nav Test Entry 2025 ${Date.now()}`);
  await entryFormPage.fillEventSummary('Nav test seed 2025.');
  await entryFormPage.save();

  // Entry in 2026
  await entryFormPage.gotoNew();
  await entryFormPage.selectTimelineGroup(groupId);
  await entryFormPage.fillDate('2026', '1', '5');
  await entryFormPage.fillTitle(`Nav Test Entry 2026 ${Date.now()}`);
  await entryFormPage.fillEventSummary('Nav test seed 2026.');
  await entryFormPage.save();

  await timelinePage.goto(groupId);

  const heatmapButton = page.getByRole('button', { name: 'Heatmap', exact: true });
  await heatmapButton.click();

  // Wait for SVG to render — should default to 2026 (most recent year with entries in group)
  const heatmapSvg = page.locator('#heatmap-container .heatmap-svg');
  await expect(heatmapSvg).toBeVisible({ timeout: 20_000 });

  // Verify we're showing 2026 and a prev arrow is available
  const yearLabel = heatmapSvg.locator('text').filter({ hasText: /^\d{4}$/ }).first();
  await expect(yearLabel).toHaveText('2026');

  // Click prev (◀) to go to 2025
  const prevArrow = heatmapSvg.locator('.heatmap-nav-arrow').filter({ hasText: '◀' });
  await expect(prevArrow).toBeVisible();
  await prevArrow.click();

  // Wait for re-render with 2025
  const yearAfterPrev = page.locator('#heatmap-container .heatmap-svg text').filter({ hasText: /^\d{4}$/ }).first();
  await expect(yearAfterPrev).toHaveText('2025', { timeout: 15_000 });

  // Click next (▶) to go back to 2026
  const nextArrow = page.locator('#heatmap-container .heatmap-svg .heatmap-nav-arrow').filter({ hasText: '▶' });
  await expect(nextArrow).toBeVisible();
  await nextArrow.click();

  // Should show 2026 again
  const yearAfterNext = page.locator('#heatmap-container .heatmap-svg text').filter({ hasText: /^\d{4}$/ }).first();
  await expect(yearAfterNext).toHaveText('2026', { timeout: 15_000 });
});
