import type { Page } from '@playwright/test';
import { expect, test } from './helpers/harness.js';
import { StoryPresentationPage } from './poms/story-presentation-page.js';

const NARRATIVE_HTML = [
  '<section class="story-section mb-4"><h2 class="h5 mb-2">Opening act</h2>',
  '<p>The first chapter sets the scene.</p></section>',
  '<section class="story-section mb-4"><h2 class="h5 mb-2">Second act</h2>',
  '<p>The second chapter raises the stakes.</p></section>',
  '<section class="story-section mb-4"><h2 class="h5 mb-2">Final act</h2>',
  '<p>The third chapter lands the story.</p></section>',
].join('');

/** Saves a three-chapter story through the app's own save route and returns its id. */
async function saveStory(page: Page, title: string): Promise<number> {
  await page.goto('/story');
  const csrfToken = await page.locator('input[name="csrf_token"]').first().inputValue();
  const response = await page.request.post('/story/save', {
    headers: { 'x-csrf-token': csrfToken },
    form: {
      title,
      format: 'executive_summary',
      narrative_html: NARRATIVE_HTML,
      narrative_text: '',
      provider_name: 'playwright',
      source_entry_count: '0',
      citations_json: '[]',
    },
    maxRedirects: 0,
  });
  expect(response.status()).toBe(303);
  const location = response.headers()['location'] ?? '';
  const match = /\/story\/(\d+)$/.exec(location);
  expect(match).not.toBeNull();
  return Number.parseInt(match?.[1] ?? '0', 10);
}

test('cinematic presentation supports keyboard navigation and hash persistence', async ({ page, e2eSession }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  const title = `${e2eSession.runId} Cinematic TS Story`;
  const storyId = await saveStory(page, title);
  const presentation = new StoryPresentationPage(page);

  await presentation.goto(storyId);
  await expect(presentation.counter).toHaveText('1 / 5');
  await expect(presentation.activeHeading).toHaveText(title);
  await expect(page).toHaveURL(/#title$/);

  await presentation.press('ArrowRight');
  await expect(presentation.counter).toHaveText('2 / 5');
  await expect(presentation.activeHeading).toHaveText('Opening act');
  await expect(page).toHaveURL(/#chapter-1$/);

  await presentation.press('Space');
  await presentation.press('PageDown');
  await expect(presentation.counter).toHaveText('4 / 5');
  await expect(presentation.activeHeading).toHaveText('Final act');

  await presentation.press('PageUp');
  await expect(presentation.activeHeading).toHaveText('Second act');
  await expect(page).toHaveURL(/#chapter-2$/);

  await page.reload();
  await expect(presentation.counter).toHaveText('3 / 5');
  await expect(presentation.activeHeading).toHaveText('Second act');

  await presentation.press('End');
  await expect(presentation.counter).toHaveText('5 / 5');
  await expect(page).toHaveURL(/#end$/);

  await presentation.press('Home');
  await expect(presentation.counter).toHaveText('1 / 5');

  await presentation.clickDot(3);
  await expect(presentation.activeHeading).toHaveText('Final act');

  await presentation.press('Escape');
  await expect(page).toHaveURL(new RegExp(`/story/${storyId}$`));
  expect(pageErrors).toEqual([]);
});

test('cinematic presentation toggles autoplay and theme', async ({ page, e2eSession }) => {
  const storyId = await saveStory(page, `${e2eSession.runId} Cinematic Controls Story`);
  const presentation = new StoryPresentationPage(page);

  await presentation.goto(storyId, 'chapter-3');
  await expect(presentation.counter).toHaveText('4 / 5');

  await expect(presentation.autoplayButton).toHaveAttribute('aria-pressed', 'false');
  await presentation.autoplayButton.click();
  await expect(presentation.autoplayButton).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('body')).toHaveClass(/is-autoplaying/);
  await presentation.press('p');
  await expect(presentation.autoplayButton).toHaveAttribute('aria-pressed', 'false');

  const html = page.locator('html');
  await expect(html).toHaveAttribute('data-presentation-theme', 'dark');
  await presentation.themeButton.click();
  await expect(html).toHaveAttribute('data-presentation-theme', 'light');
  await page.reload();
  await expect(html).toHaveAttribute('data-presentation-theme', 'light');
});
