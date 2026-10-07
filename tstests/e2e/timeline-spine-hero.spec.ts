// Timeline hero header and spine
// 1. Seed two tagged entries in a dedicated group.
// 2. Open the timeline and assert the hero title, stat tiles, sparkline and top tag pills.
// 3. Assert the Details view renders a spine with one tag-colored node per entry and a sticky month marker.
// 4. Assert reveal-on-scroll leaves cards fully visible once in view.
// 5. Assert reduced motion opts out of the reveal animation entirely.
// 6. Assert the mobile layout has no horizontal scroll.

import { expect, test } from './helpers/harness.js';
import { EntryFormPage } from './poms/entry-form-page.js';
import { TimelinePage } from './poms/timeline-page.js';

test('timeline hero stats and spine render with tag colors', async ({ ensureDedicatedGroup, page }) => {
  const groupId = await ensureDedicatedGroup();
  const entryFormPage = new EntryFormPage(page);
  const timelinePage = new TimelinePage(page);
  const seeds = [
    { day: '3', title: `Spine Alpha ${Date.now()}`, tags: 'spine-alpha, shared-tag' },
    { day: '18', title: `Spine Beta ${Date.now()}`, tags: 'spine-beta, shared-tag' },
  ];

  for (const seed of seeds) {
    await entryFormPage.gotoNew();
    await entryFormPage.selectTimelineGroup(groupId);
    await entryFormPage.fillDate('2026', '5', seed.day);
    await entryFormPage.fillTitle(seed.title);
    await entryFormPage.fillEventSummary('Spine and hero seed entry.');
    await entryFormPage.fillTags(seed.tags);
    await entryFormPage.save();
    await expect(page).toHaveURL(/\/entries\/\d+\/view$/);
  }

  await timelinePage.goto(groupId);

  // Hero: confident title and stat tiles.
  await expect(timelinePage.heading).toContainText('Timeline');
  const titleSize = await timelinePage.heading.evaluate((node) => parseFloat(getComputedStyle(node).fontSize));
  expect(titleSize).toBeGreaterThanOrEqual(28);

  const stats = page.getByRole('region', { name: 'Timeline at a glance' });
  await expect(stats).toBeVisible();
  await expect(stats.locator('[data-tl-count]').first()).toHaveText('2');
  await expect(stats.locator('.tl-sparkline')).toBeVisible();
  const topTag = stats.locator('.tl-stat-tag-list .tag-pill').first();
  await expect(topTag).toHaveText('shared-tag');

  // Spine: one node per entry, colored from the tag palette.
  const spineItems = page.locator('[data-detail-groups] .timeline-spine-item');
  await expect(spineItems).toHaveCount(2);
  const firstNode = spineItems.first().locator('.timeline-spine-node');
  await expect(firstNode).toBeVisible();
  const nodeColor = await firstNode.evaluate((node) => getComputedStyle(node).backgroundColor);
  expect(nodeColor).not.toBe('rgba(0, 0, 0, 0)');
  await expect(spineItems.first().locator('a[href^="/entries/"]').first()).toBeVisible();
  await expect(spineItems.first().locator('.tag-pill', { hasText: 'shared-tag' })).toBeVisible();

  const marker = page.locator('[data-detail-groups] .timeline-spine-marker').first();
  await expect(marker).toContainText('May 2026');
  expect(await marker.evaluate((node) => getComputedStyle(node).position)).toBe('sticky');

  // Reveal-on-scroll settles to fully visible content.
  await expect(spineItems.first()).not.toHaveClass(/tl-pending/);
  await expect.poll(async () => spineItems.first().evaluate((node) => getComputedStyle(node).opacity)).toBe('1');

  // Reduced motion: no motion class, nothing hidden.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.reload();
  await expect(page.locator('html')).not.toHaveClass(/tl-motion/);
  await expect(page.locator('.tl-pending')).toHaveCount(0);

  // Mobile: no horizontal scroll.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.reload();
  await expect(spineItems.first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
