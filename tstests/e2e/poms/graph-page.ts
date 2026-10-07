import type { Locator, Page } from '@playwright/test';

/** Encapsulates the Connection Graph and Tag Clusters pages (shared graph shell). */
export class GraphPage {
  readonly shell: Locator;
  readonly status: Locator;
  readonly nodes: Locator;
  readonly visibleNodes: Locator;
  readonly links: Locator;
  readonly hiddenLinks: Locator;
  readonly panel: Locator;
  readonly panelTitle: Locator;
  readonly panelClose: Locator;
  readonly zoomIn: Locator;
  readonly zoomOut: Locator;
  readonly fit: Locator;
  readonly legend: Locator;
  readonly playButton: Locator;
  readonly scrubber: Locator;
  readonly timelapseDate: Locator;
  readonly timelapseCount: Locator;

  constructor(private readonly page: Page) {
    this.shell = page.locator('[data-eg-shell]');
    this.status = this.shell.locator('[data-eg-status]');
    this.nodes = this.shell.locator('g.eg-node');
    this.visibleNodes = this.shell.locator('g.eg-node:not(.is-hidden)');
    this.links = this.shell.locator('path.eg-link');
    this.hiddenLinks = this.shell.locator('path.eg-link.is-hidden');
    this.panel = this.shell.locator('[data-eg-panel]');
    this.panelTitle = this.panel.locator('[data-eg-panel-title]');
    this.panelClose = this.panel.getByRole('button', { name: 'Close details panel' });
    this.zoomIn = this.shell.getByRole('button', { name: 'Zoom in' });
    this.zoomOut = this.shell.getByRole('button', { name: 'Zoom out' });
    this.fit = this.shell.getByRole('button', { name: 'Fit graph to screen' });
    this.legend = this.shell.locator('[data-eg-legend]');
    this.playButton = this.shell.locator('[data-eg-play]');
    this.scrubber = this.shell.getByRole('slider', { name: 'Time-lapse date' });
    this.timelapseDate = this.shell.locator('[data-eg-date]');
    this.timelapseCount = this.shell.locator('[data-eg-count]');
  }

  async gotoConnections(groupId: number): Promise<void> {
    await this.page.goto(`/groups/${groupId}/connections/graph`);
  }

  async gotoTopics(groupId: number): Promise<void> {
    await this.page.goto(`/groups/${groupId}/topics/graph`);
  }

  /** Wait until the engine finished its initial render. */
  async waitForReady(): Promise<void> {
    await this.shell.and(this.page.locator('[data-eg-ready="true"]')).waitFor();
  }

  node(id: string | number): Locator {
    return this.shell.locator(`g.eg-node[data-node-id="${id}"]`);
  }

  /** Move the time-lapse scrubber to an offset (days from the earliest entry). */
  async scrubTo(offset: number): Promise<void> {
    await this.scrubber.fill(String(offset));
  }
}
