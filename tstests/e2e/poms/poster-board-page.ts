import type { Locator, Page } from '@playwright/test';

/** Encapsulates the poster board page and its HTML export. */
export class PosterBoardPage {
  readonly heading: Locator;
  readonly pinnedCount: Locator;
  readonly categoryChips: Locator;
  readonly searchInput: Locator;
  readonly posters: Locator;
  readonly emptyMessage: Locator;
  readonly exportLink: Locator;
  readonly timelineLink: Locator;
  readonly dialog: Locator;
  readonly dialogHeading: Locator;
  readonly nextButton: Locator;
  readonly closeButton: Locator;
  readonly stringToggle: Locator;
  readonly strings: Locator;
  readonly tacks: Locator;

  constructor(private readonly page: Page) {
    this.heading = page.getByRole('heading', { level: 1 });
    this.pinnedCount = page.locator('#count');
    this.categoryChips = page.getByRole('group', { name: 'Filter by category' });
    this.searchInput = page.getByRole('searchbox', { name: 'Search the board' });
    this.posters = page.locator('.poster.in .paper');
    this.emptyMessage = page.locator('#empty');
    this.exportLink = page.getByRole('link', { name: 'Export HTML' });
    this.timelineLink = page.getByRole('link', { name: /Timeline/ });
    this.dialog = page.getByRole('dialog', { name: 'Event detail' });
    this.dialogHeading = this.dialog.getByRole('heading', { level: 2 });
    this.nextButton = this.dialog.getByRole('button', { name: /Next/ });
    this.closeButton = this.dialog.getByRole('button', { name: 'Close' });
    this.stringToggle = page.getByRole('button', { name: /Red string/ });
    this.strings = page.locator('svg.strings .yarn');
    this.tacks = page.locator('svg.strings .tack');
  }

  /** Opens the poster board for the selected group. */
  async goto(groupId: number | string): Promise<void> {
    await this.page.goto(`/timeline/board?group_id=${groupId}`);
  }

  /** Returns the poster whose headline contains the given text. */
  poster(headline: string): Locator {
    return this.posters.filter({ hasText: headline });
  }

  /** Narrows the board with the client-side search box. */
  async search(text: string): Promise<void> {
    await this.searchInput.fill(text);
  }

  /** Returns the red string tying two entries together, in either direction. */
  string(firstId: number, secondId: number): Locator {
    const [a, b] = firstId < secondId ? [firstId, secondId] : [secondId, firstId];
    return this.page.locator(`svg.strings .yarn[data-a="${a}"][data-b="${b}"]`);
  }

  /** Filters the board to one category chip. */
  async chooseCategory(name: string): Promise<void> {
    await this.categoryChips.getByRole('button', { name: new RegExp(`^${name}`, 'i') }).click();
  }
}
