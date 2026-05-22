---
name: playwright-typescript-testing
description: Playwright test automation with TypeScript. Use when writing E2E tests, organizing test suites, implementing locators and assertions, or setting up form interactions and element testing. Covers web-first assertions, semantic locators, test hooks, and test organization patterns.
---

# Playwright TypeScript Testing

Test automation for web applications using Playwright's TypeScript API.

## When to Apply

- Writing E2E tests with type safety and auto-completion
- Organizing test suites with describe blocks and fixtures  
- Implementing form interactions and element assertions
- Setting up test hooks for authentication or cleanup

## Critical Rules

**Use semantic locators over CSS selectors**: Role-based locators are resilient to UI changes

```typescript
// WRONG - brittle CSS selector
await page.locator('#submit-btn').click();

// RIGHT - semantic role locator
await page.getByRole('button', { name: 'Submit' }).click();
```

**Leverage auto-waiting assertions**: Never add manual waits when web-first assertions handle timing

```typescript
// WRONG - manual wait
await page.waitForTimeout(2000);
expect(await page.locator('.status').textContent()).toBe('Complete');

// RIGHT - auto-waiting assertion
await expect(page.locator('.status')).toHaveText('Complete');
```

## Key Patterns

### Basic Test Structure

```typescript
import { test, expect } from '@playwright/test';

test.describe('User Authentication', () => {
  test('successful login', async ({ page }) => {
    await page.goto('https://example.com/login');
    
    await page.getByLabel('Username').fill('user@example.com');
    await page.getByLabel('Password').fill('password123');
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    await expect(page.getByText('Welcome')).toBeVisible();
    await expect(page).toHaveURL(/dashboard/);
  });
});
```

### Form Interactions

```typescript
// Text inputs and textareas
await page.getByLabel('Email').fill('test@example.com');
await page.getByRole('textbox', { name: 'Description' }).fill('Multi-line text');

// Checkboxes and radio buttons
await page.getByRole('checkbox', { name: 'Terms' }).check();
await page.getByRole('radio', { name: 'Premium' }).click();

// Dropdowns
await page.getByLabel('Country').selectOption('US');
await page.getByLabel('Colors').selectOption(['red', 'blue']);

// File uploads
await page.getByLabel('Upload').setInputFiles('document.pdf');
```

### Web-First Assertions

```typescript
// Element state
await expect(page.locator('.loading')).toBeHidden();
await expect(page.getByRole('button', { name: 'Submit' })).toBeEnabled();
await expect(page.getByRole('checkbox')).toBeChecked();

// Text content
await expect(page.getByRole('heading')).toHaveText('Dashboard');
await expect(page.locator('.message')).toContainText('Success');

// Element properties
await expect(page.getByLabel('Email')).toHaveValue('user@example.com');
await expect(page.locator('.alert')).toHaveClass(/error/);
await expect(page.locator('.button')).toHaveAttribute('disabled');

// Page state
await expect(page).toHaveTitle(/Dashboard/);
await expect(page).toHaveURL('https://example.com/dashboard');

// Element count
await expect(page.locator('.item')).toHaveCount(3);
```

### Test Hooks and Organization

```typescript
test.describe('Todo Management', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('https://example.com/todos');
    await page.getByLabel('Email').fill('test@example.com');
    await page.getByLabel('Password').fill('password');
    await page.getByRole('button', { name: 'Login' }).click();
  });

  test.afterEach(async ({ page }, testInfo) => {
    if (testInfo.status !== testInfo.expectedStatus) {
      await page.screenshot({ 
        path: `screenshots/${testInfo.title}-failure.png` 
      });
    }
  });

  test('add new todo', async ({ page }) => {
    await page.getByPlaceholder('What needs to be done?').fill('Buy groceries');
    await page.getByPlaceholder('What needs to be done?').press('Enter');
    
    await expect(page.getByText('Buy groceries')).toBeVisible();
    await expect(page.getByText('1 item left')).toBeVisible();
  });
});
```

### Custom Fixtures

```typescript
// fixtures.ts
import { test as base } from '@playwright/test';

type TestFixtures = {
  authenticatedPage: Page;
};

export const test = base.extend<TestFixtures>({
  authenticatedPage: async ({ page }, use) => {
    await page.goto('https://example.com/login');
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password').fill('password');
    await page.getByRole('button', { name: 'Login' }).click();
    await use(page);
  },
});

// test file
import { test } from './fixtures';

test('admin dashboard', async ({ authenticatedPage }) => {
  await expect(authenticatedPage.getByText('Admin Panel')).toBeVisible();
});
```

### Keyboard and Mouse Interactions

```typescript
// Keyboard actions
await page.getByRole('textbox').press('Enter');
await page.getByRole('textbox').press('Control+A');
await page.getByLabel('Search').pressSequentially('playwright', { delay: 100 });

// Mouse actions
await page.getByRole('button').click();
await page.getByRole('button').dblclick();
await page.getByText('Menu Item').hover();
await page.getByRole('button').click({ button: 'right' });
```

## Common Mistakes

- **Using CSS selectors instead of semantic locators** — Use `getByRole`, `getByLabel`, `getByText` for maintainable tests
- **Adding manual waits** — Playwright auto-waits; use web-first assertions instead of `waitForTimeout`
- **Not using test hooks for setup** — Use `beforeEach` for common authentication or navigation
- **Ignoring test isolation** — Each test should be independent; don't rely on test execution order
- **Missing await keywords** — Configure ESLint with `@typescript-eslint/no-floating-promises` to catch this