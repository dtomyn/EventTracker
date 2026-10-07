import type { Locator, Page } from '@playwright/test';

/** Encapsulates the cinematic Story Mode presentation at /story/{id}/presentation. */
export class StoryPresentationPage {
  readonly counter: Locator;
  readonly activeSlide: Locator;
  readonly activeHeading: Locator;
  readonly autoplayButton: Locator;
  readonly themeButton: Locator;

  constructor(private readonly page: Page) {
    this.counter = page.locator('[data-cine-counter]');
    this.activeSlide = page.locator('[data-cine-slide].is-active');
    this.activeHeading = this.activeSlide.locator('.cine-heading, .cine-title');
    this.autoplayButton = page.locator('[data-cine-autoplay]');
    this.themeButton = page.locator('[data-cine-theme]');
  }

  /** Opens the presentation for a saved story, optionally at a slide hash. */
  async goto(storyId: number, hash = ''): Promise<void> {
    await this.page.goto(`/story/${storyId}/presentation${hash ? `#${hash}` : ''}`);
  }

  /** Presses a single keyboard key on the presentation. */
  async press(key: string): Promise<void> {
    await this.page.keyboard.press(key);
  }

  /** Clicks the chapter dot at the provided slide index. */
  async clickDot(index: number): Promise<void> {
    await this.page.locator(`[data-cine-dot="${index}"]`).click();
  }
}
