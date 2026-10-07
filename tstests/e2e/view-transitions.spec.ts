// Cross-document view transitions: timeline -> entry detail -> back.
// Asserts the navigation produces no console errors (for example duplicate
// view-transition-name) and that dynamically assigned names are cleared.

import type { Page } from '@playwright/test';

import { expect, test } from './helpers/harness.js';

const namedElementsScript = () =>
  Array.from(document.querySelectorAll<HTMLElement>('*'))
    .map((element) => element.style.viewTransitionName)
    .filter((name) => name && name.startsWith('entry-'));

async function expectNoEntryNames(page: Page) {
  await expect.poll(() => page.evaluate(namedElementsScript)).toEqual([]);
}

test('timeline to entry and back keeps view transitions clean', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => consoleErrors.push(error.message));

  await page.goto('/');
  const viewLink = page.locator('[data-entry-card] a[href$="/view"]').first();
  await expect(viewLink).toBeVisible();
  const href = await viewLink.getAttribute('href');
  expect(href).toMatch(/^\/entries\/\d+\/view$/);

  await viewLink.click();
  // The timeline may open an inline preview first; follow its full-page link.
  await page.waitForURL(/\/entries\/\d+\/view$|[?&]preview=\d+/);
  if (!/\/entries\/\d+\/view$/.test(page.url())) {
    await page.locator(`a[href="${href}"]`).filter({ hasText: /full page/i }).first().click();
  }
  await expect(page).toHaveURL(new RegExp(`${href}$`));
  await expect(page.locator('[data-entry-transition-target]')).toBeVisible();
  await expectNoEntryNames(page);

  await page.goBack();
  await expect(page).not.toHaveURL(/\/entries\/\d+\/view$/);
  await expect(page.locator('[data-entry-card]').first()).toBeVisible();
  await expectNoEntryNames(page);

  expect(consoleErrors.filter((text) => !/favicon/i.test(text))).toEqual([]);
});
