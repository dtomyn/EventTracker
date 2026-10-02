// Poster board: open from the timeline, filter, read a poster, export a
// self-contained HTML hand-off and confirm it works without the app.

import { readFile } from 'node:fs/promises';

import { expect, test } from './helpers/harness.js';
import { EntryFormPage } from './poms/entry-form-page.js';
import { PosterBoardPage } from './poms/poster-board-page.js';
import { TimelinePage } from './poms/timeline-page.js';

test('poster board shows scoped events and exports a self-contained HTML file', async ({
  ensureDedicatedGroup,
  page,
}) => {
  const groupId = await ensureDedicatedGroup();
  const runToken = Date.now();
  const policyTitle = `Board policy event ${runToken}`;
  const launchTitle = `Board launch event ${runToken}`;
  const entryFormPage = new EntryFormPage(page);
  const board = new PosterBoardPage(page);

  for (const [title, day, tags] of [
    [policyTitle, '3', 'policy'],
    [launchTitle, '9', 'launch'],
  ] as const) {
    await entryFormPage.gotoNew();
    await entryFormPage.selectTimelineGroup(groupId);
    await entryFormPage.fillDate('2026', '4', day);
    await entryFormPage.fillTitle(title);
    await entryFormPage.fillEventSummary(`${title} summary for the poster board.`);
    await entryFormPage.fillTags(tags);
    await entryFormPage.save();
    await expect(page).toHaveURL(/\/entries\/\d+\/view$/);
  }

  await new TimelinePage(page).goto(groupId);
  await page.getByRole('link', { name: 'Poster Board' }).click();
  await expect(page).toHaveURL(new RegExp(`/timeline/board\\?group_id=${groupId}$`));
  await expect(page).toHaveTitle(/Poster Board$/);

  await expect(board.poster(policyTitle)).toBeVisible();
  await expect(board.poster(launchTitle)).toBeVisible();
  await expect(board.pinnedCount).toHaveText('2');

  await board.chooseCategory('policy');
  await expect(board.poster(launchTitle)).toHaveCount(0);
  await expect(board.poster(policyTitle)).toBeVisible();
  await board.chooseCategory('All');

  await board.search('no such poster');
  await expect(board.emptyMessage).toBeVisible();
  await board.search('');
  await expect(board.posters).toHaveCount(2);

  await board.poster(launchTitle).click();
  await expect(board.dialog).toBeVisible();
  await expect(board.dialogHeading).toHaveText(launchTitle);
  await expect(board.dialog.getByRole('link', { name: /Open full entry/ })).toBeVisible();
  await board.nextButton.click();
  await expect(board.dialogHeading).toHaveText(policyTitle);
  await board.closeButton.click();
  await expect(board.dialog).toBeHidden();

  const downloadPromise = page.waitForEvent('download');
  await board.exportLink.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^EventTracker-board-.+\.html$/);
  const exportPath = await download.path();
  const exportedHtml = await readFile(exportPath, 'utf-8');

  // The exported file must render on its own: block every request while it loads.
  await page.route('**/*', (route) => route.abort());
  await page.setContent(exportedHtml);
  await expect(board.poster(policyTitle)).toBeVisible();
  await expect(board.poster(launchTitle)).toBeVisible();
  await expect(board.exportLink).toHaveCount(0);

  await board.poster(policyTitle).click();
  await expect(board.dialogHeading).toHaveText(policyTitle);
  await expect(board.dialog.getByRole('link', { name: /Open full entry/ })).toHaveCount(0);
});
