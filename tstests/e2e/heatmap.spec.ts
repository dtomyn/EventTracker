// Heatmap feature E2E tests
// 1. Heatmap tab is visible and clickable in the toolbar.
// 2. Clicking heatmap tab renders the SVG grid.
// 3. Hovering a cell shows the tooltip with date, count, and titles.
// 4. Clicking a cell with entries shows filtered entries below.
// 5. Year navigation arrows work.
// 6. Heatmap legend is visible.
// 7. Time-lapse playback and scrubber reveal cells week by week.
// 8. Reduced motion disables auto playback but keeps the scrubber working.

import { expect, test } from './helpers/harness.js';
import { EntryFormPage } from './poms/entry-form-page.js';
import { TimelinePage } from './poms/timeline-page.js';

type Page = import('@playwright/test').Page;

/**
 * Helper: seed a minimal entry so that the timeline toolbar (and heatmap tab) render.
 */
async function seedEntryAndGoToHeatmap(
  page: Page,
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

async function openHeatmap(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Heatmap', exact: true }).click();
  await expect(page.locator('#heatmap-container .heatmap-svg')).toBeVisible();
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

  await seedEntryAndGoToHeatmap(page, groupId);
  await openHeatmap(page);

  await expect(page.locator('#heatmap-view')).toBeVisible();
  await expect(page.locator('.heatmap-cell')).toHaveCount(365);

  const heatmapButton = page.getByRole('button', { name: 'Heatmap', exact: true });
  await expect(heatmapButton).toHaveAttribute('aria-pressed', 'true');
  await expect(heatmapButton).not.toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('#heatmap-container')).toHaveAttribute('aria-busy', 'false');

  await expect(page.locator('[data-current-view-label]')).toHaveText('Heatmap');
});

test('heatmap shows a skeleton while loading', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  await seedEntryAndGoToHeatmap(page, groupId);

  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/heatmap**', async (route) => {
    await gate;
    await route.continue();
  });

  await page.getByRole('button', { name: 'Heatmap', exact: true }).click();
  await expect(page.locator('#heatmap-container .hm-skeleton')).toBeVisible();
  await expect(page.locator('#heatmap-container')).toHaveAttribute('aria-busy', 'true');
  await expect(page.getByRole('button', { name: 'Heatmap', exact: true })).toHaveAttribute('aria-busy', 'true');

  release();
  await expect(page.locator('#heatmap-container .heatmap-svg')).toBeVisible();
  await expect(page.locator('#heatmap-container .hm-skeleton')).toHaveCount(0);
});

test('heatmap legend is visible after grid renders', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();

  await seedEntryAndGoToHeatmap(page, groupId);
  await openHeatmap(page);

  const legend = page.locator('#heatmap-container .hm-legend');
  await expect(legend.getByText('Less', { exact: true })).toBeVisible();
  await expect(legend.getByText('More', { exact: true })).toBeVisible();
  await expect(legend.locator('.hm-legend-swatch')).toHaveCount(5);
});

test('hovering a heatmap cell shows tooltip with date, count and titles', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  const entryTitle = `Heatmap Tooltip ${Date.now()}`;

  await seedEntryAndGoToHeatmap(page, groupId, '2025', '6', '15', entryTitle);
  await openHeatmap(page);

  const tooltip = page.locator('.heatmap-tooltip');

  await page.locator('.heatmap-cell').first().hover();
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText('No entries');

  await page.locator('.heatmap-cell[data-date="2025-06-15"]').hover();
  await expect(tooltip).toContainText('June 15, 2025');
  await expect(tooltip).toContainText('1 entry');
  await expect(tooltip).toContainText(entryTitle);

  await page.mouse.move(0, 0);
  await expect(tooltip).toBeHidden();
});

test('clicking a cell with entries shows filtered entries below', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  const entryTitle = `Heatmap Cell Test ${Date.now()}`;

  await seedEntryAndGoToHeatmap(page, groupId, '2025', '6', '15', entryTitle);
  await openHeatmap(page);
  await expect(page.locator('#heatmap-container .hm-year')).toHaveText('2025');

  const targetCell = page.locator('.heatmap-cell[data-date="2025-06-15"]');
  await targetCell.click();

  const entriesContainer = page.locator('#heatmap-entries');
  await expect(entriesContainer).toContainText(entryTitle);
  await expect(targetCell).toHaveClass(/heatmap-cell-selected/);

  // Keyboard: Enter on the focused cell toggles the filter off again.
  await targetCell.focus();
  await page.keyboard.press('Enter');
  await expect(entriesContainer).toBeEmpty();
  await expect(targetCell).not.toHaveClass(/heatmap-cell-selected/);
});

test('arrow keys move focus between heatmap days', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();

  await seedEntryAndGoToHeatmap(page, groupId, '2025', '6', '15');
  await openHeatmap(page);

  const startCell = page.locator('.heatmap-cell[data-date="2025-06-15"]');
  await expect(startCell).toHaveAttribute('tabindex', '0');
  await startCell.focus();

  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.heatmap-cell[data-date="2025-06-16"]')).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.heatmap-cell[data-date="2025-06-23"]')).toBeFocused();
  await expect(page.locator('.heatmap-tooltip')).toContainText('June 23, 2025');
});

test('year navigation arrows load adjacent year', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();

  const entryFormPage = new EntryFormPage(page);
  const timelinePage = new TimelinePage(page);

  for (const [year, month, day] of [['2024', '3', '10'], ['2025', '6', '15'], ['2026', '1', '5']] as const) {
    await entryFormPage.gotoNew();
    await entryFormPage.selectTimelineGroup(groupId);
    await entryFormPage.fillDate(year, month, day);
    await entryFormPage.fillTitle(`Nav Test Entry ${year} ${Date.now()}`);
    await entryFormPage.fillEventSummary(`Nav test seed ${year}.`);
    await entryFormPage.save();
  }

  await timelinePage.goto(groupId);
  await openHeatmap(page);

  const yearLabel = page.locator('#heatmap-container .hm-year');
  await expect(yearLabel).toHaveText('2026');
  await expect(page.getByRole('button', { name: /^Next year/ })).toBeDisabled();

  await page.getByRole('button', { name: 'Previous year (2025)' }).click();
  await expect(yearLabel).toHaveText('2025');

  await page.getByRole('button', { name: 'Next year (2026)' }).click();
  await expect(yearLabel).toHaveText('2026');
});

test('time-lapse scrubber reveals cells week by week', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();

  await seedEntryAndGoToHeatmap(page, groupId, '2025', '6', '15');
  await openHeatmap(page);

  const scrubber = page.getByRole('slider', { name: /Time-lapse position/ });
  const counter = page.locator('#heatmap-container .hm-counter');
  const cell = page.locator('.heatmap-cell[data-date="2025-06-15"]');

  await expect(counter).toHaveText('1 event through Dec 31, 2025');
  await expect(cell).not.toHaveClass(/is-future/);

  // 2025 starts on a Wednesday, so Jun 15 (a Sunday) falls in week index 23.
  await scrubber.fill('23');
  await expect(cell).toHaveClass(/is-future/);
  await expect(counter).toHaveText('0 events through Jun 8, 2025');

  await scrubber.fill('24');
  await expect(cell).not.toHaveClass(/is-future/);
  await expect(counter).toHaveText('1 event through Jun 15, 2025');
  await expect(scrubber).toHaveAttribute('aria-valuetext', '1 event through Jun 15, 2025');

  // Keyboard scrubbing works too.
  await scrubber.focus();
  await page.keyboard.press('Home');
  await expect(counter).toHaveText('0 events before Jan 1, 2025');
});

test('time-lapse playback animates through the year and can pause', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();

  await seedEntryAndGoToHeatmap(page, groupId, '2025', '6', '15');
  await openHeatmap(page);

  const playButton = page.getByRole('button', { name: 'Play time-lapse' });
  const scrubber = page.getByRole('slider', { name: /Time-lapse position/ });
  await expect(playButton).toBeEnabled();

  await playButton.click();
  const pauseButton = page.getByRole('button', { name: 'Pause time-lapse' });
  await expect(pauseButton).toHaveAttribute('aria-pressed', 'true');

  // Playback restarts from week 0 and advances; pause right away so a slow runner cannot finish the year first.
  await expect.poll(async () => Number(await scrubber.inputValue())).toBeGreaterThan(0);
  await pauseButton.click();
  await expect(page.getByRole('button', { name: 'Play time-lapse' })).toHaveAttribute('aria-pressed', 'false');

  const pausedAt = Number(await scrubber.inputValue());
  expect(pausedAt).toBeLessThan(53);
  await page.waitForTimeout(500);
  expect(Number(await scrubber.inputValue())).toBe(pausedAt);

  // Resume and let it finish.
  await page.getByRole('button', { name: 'Play time-lapse' }).click();
  await expect(page.locator('#heatmap-container .hm-counter')).toHaveText('1 event through Dec 31, 2025', { timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Play time-lapse' })).toBeVisible();
});

test('reduced motion disables playback but the scrubber still works', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();

  await seedEntryAndGoToHeatmap(page, groupId, '2025', '6', '15');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openHeatmap(page);

  await expect(page.getByRole('button', { name: 'Play time-lapse' })).toBeDisabled();

  await page.getByRole('slider', { name: /Time-lapse position/ }).fill('10');
  await expect(page.locator('#heatmap-container .hm-counter')).toHaveText('0 events through Mar 9, 2025');
});
