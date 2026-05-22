# Developer Aesthetic Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform EventTracker's frontend from generic Bootstrap to a distinctive developer/technical aesthetic with cyan/teal accents, Inter typography, and subtle-border component style — across both dark and light modes.

**Architecture:** CSS-first redesign. ~80% of changes are in `app/static/styles.css` (CSS custom property remapping and component restyling). One template change adds font imports to `base.html`. One partial gets a monospace class on date elements. No JavaScript, backend, or database changes.

**Tech Stack:** Bootstrap 5.3.3 (existing), Inter + JetBrains Mono via Google Fonts CDN, CSS custom properties.

**Spec:** `docs/superpowers/specs/2026-04-05-developer-aesthetic-redesign.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/templates/base.html` | Modify | Add Google Fonts imports (Inter, JetBrains Mono), update navbar classes |
| `app/static/styles.css` | Modify | Full CSS variable rewrite, component restyling, typography |
| `app/templates/partials/entry_card.html` | Modify | Add `font-mono` class to date span for JetBrains Mono |

---

### Task 1: Add Google Fonts imports to base.html

**Files:**
- Modify: `app/templates/base.html:8-10`

- [ ] **Step 1: Add font preconnect and import links**

In `app/templates/base.html`, replace lines 8-10:

```html
    <link rel="preconnect" href="https://cdn.jsdelivr.net">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
    <link rel="stylesheet" href="{{ url_for('static', path='/styles.css') }}">
```

With:

```html
    <link rel="preconnect" href="https://cdn.jsdelivr.net">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
    <link rel="stylesheet" href="{{ url_for('static', path='/styles.css') }}">
```

- [ ] **Step 2: Verify dev server loads fonts**

Run: `uv run python -m scripts.run_dev --reload`

Open `http://127.0.0.1:35231/` in browser. Open DevTools Network tab, filter by "font". Confirm Inter and JetBrains Mono font files load successfully (200 status).

- [ ] **Step 3: Commit**

```bash
git add app/templates/base.html
git commit -m "feat: add Inter and JetBrains Mono font imports to base template"
```

---

### Task 2: Rewrite CSS custom properties — light mode

**Files:**
- Modify: `app/static/styles.css:1-49`

- [ ] **Step 1: Replace light mode CSS variables**

In `app/static/styles.css`, replace the entire `:root, [data-bs-theme="light"]` block (lines 1-49) with:

```css
:root,
[data-bs-theme="light"] {
    /* Backgrounds */
    --et-bg-gradient-accent: rgba(8, 145, 178, 0.06);
    --et-bg-gradient-start: #f8fafc;
    --et-bg-gradient-end: #f1f5f9;

    /* Text */
    --et-text: #0f172a;
    --et-text-secondary: #475569;
    --et-text-muted: #94a3b8;
    --et-text-subtle: #334155;
    --et-text-date: #0891b2;
    --et-text-sep: #94a3b8;

    /* Borders */
    --et-border: rgba(15, 23, 42, 0.1);
    --et-border-light: rgba(15, 23, 42, 0.07);
    --et-border-dashed: rgba(15, 23, 42, 0.12);
    --et-border-input: rgba(15, 23, 42, 0.1);

    /* Primary accent — cyan */
    --et-primary: #0891b2;
    --et-primary-bg: rgba(8, 145, 178, 0.08);
    --et-primary-border: rgba(8, 145, 178, 0.18);
    --et-primary-border-hover: rgba(8, 145, 178, 0.34);
    --et-primary-focus: rgba(8, 145, 178, 0.24);
    --et-primary-focus-shadow: rgba(8, 145, 178, 0.2);
    --et-primary-active-shadow: rgba(8, 145, 178, 0.12);
    --et-primary-outline-focus: rgba(8, 145, 178, 0.28);
    --et-primary-line-start: rgba(8, 145, 178, 0.4);
    --et-primary-line-end: rgba(8, 145, 178, 0.1);

    /* Node */
    --et-node-border: #fff;

    /* Cards & surfaces */
    --et-card-bg: #ffffff;
    --et-card-bg-solid: #ffffff;
    --et-card-bg-glass: rgba(255, 255, 255, 0.85);
    --et-card-bg-subtle: rgba(248, 250, 252, 0.8);
    --et-card-bg-empty: rgba(248, 250, 252, 0.9);
    --et-card-bg-empty-viz: rgba(248, 250, 252, 0.85);
    --et-surface-bg: #f8fafc;
    --et-surface-bg-alt: #f1f5f9;
    --et-surface-bg-subtle: rgba(15, 23, 42, 0.03);
    --et-card-shadow: rgba(15, 23, 42, 0.05);
    --et-card-shadow-hover: rgba(15, 23, 42, 0.08);
    --et-summary-gradient-start: #ffffff;
    --et-summary-gradient-end: #f8fafc;

    /* Progress mode */
    --et-progress-mode-border: rgba(15, 23, 42, 0.12);
    --et-progress-mode-bg: #ffffff;
    --et-progress-mode-text: #334155;
    --et-progress-mode-active-border: #0891b2;
    --et-progress-mode-active-bg: #0891b2;
    --et-progress-mode-active-text: #fff;
    --et-progress-status: #0f172a;

    /* Semantic colors */
    --et-error: #dc2626;
    --et-success: #059669;
    --et-warning: #d97706;
    --et-mark-bg: #ccfbf1;
}
```

- [ ] **Step 2: Visually check light mode**

Run: `uv run python -m scripts.run_dev --reload`

Open `http://127.0.0.1:35231/`, ensure light mode is active (click toggle if needed). Verify:
- Page background is light slate (#f8fafc)
- Cards have white backgrounds with subtle borders
- Accent colors are cyan (#0891b2) not blue
- Text is readable with good contrast

- [ ] **Step 3: Commit**

```bash
git add app/static/styles.css
git commit -m "feat: rewrite light mode CSS variables to cyan/teal palette"
```

---

### Task 3: Rewrite CSS custom properties — dark mode

**Files:**
- Modify: `app/static/styles.css:51-98` (the `[data-bs-theme="dark"]` block)

- [ ] **Step 1: Replace dark mode CSS variables**

In `app/static/styles.css`, replace the entire `[data-bs-theme="dark"]` block (lines 51-98) with:

```css
[data-bs-theme="dark"] {
    /* Backgrounds */
    --et-bg-gradient-accent: rgba(6, 182, 212, 0.05);
    --et-bg-gradient-start: #0f172a;
    --et-bg-gradient-end: #0c1220;

    /* Text */
    --et-text: #f1f5f9;
    --et-text-secondary: #94a3b8;
    --et-text-muted: #64748b;
    --et-text-subtle: #cbd5e1;
    --et-text-date: #06b6d4;
    --et-text-sep: #334155;

    /* Borders — cyan-tinted */
    --et-border: rgba(6, 182, 212, 0.15);
    --et-border-light: rgba(6, 182, 212, 0.1);
    --et-border-dashed: rgba(6, 182, 212, 0.18);
    --et-border-input: rgba(6, 182, 212, 0.12);

    /* Primary accent — cyan */
    --et-primary: #06b6d4;
    --et-primary-bg: rgba(6, 182, 212, 0.1);
    --et-primary-border: rgba(6, 182, 212, 0.2);
    --et-primary-border-hover: rgba(6, 182, 212, 0.35);
    --et-primary-focus: rgba(6, 182, 212, 0.25);
    --et-primary-focus-shadow: rgba(6, 182, 212, 0.2);
    --et-primary-active-shadow: rgba(6, 182, 212, 0.15);
    --et-primary-outline-focus: rgba(6, 182, 212, 0.3);
    --et-primary-line-start: rgba(6, 182, 212, 0.4);
    --et-primary-line-end: rgba(6, 182, 212, 0.1);

    /* Node */
    --et-node-border: #1e293b;

    /* Cards & surfaces */
    --et-card-bg: #1e293b;
    --et-card-bg-solid: #1e293b;
    --et-card-bg-glass: rgba(30, 41, 59, 0.85);
    --et-card-bg-subtle: rgba(30, 41, 59, 0.7);
    --et-card-bg-empty: rgba(30, 41, 59, 0.8);
    --et-card-bg-empty-viz: rgba(30, 41, 59, 0.75);
    --et-surface-bg: #1e293b;
    --et-surface-bg-alt: #162032;
    --et-surface-bg-subtle: rgba(6, 182, 212, 0.04);
    --et-card-shadow: none;
    --et-card-shadow-hover: rgba(0, 0, 0, 0.3);
    --et-summary-gradient-start: #1e293b;
    --et-summary-gradient-end: #162032;

    /* Progress mode */
    --et-progress-mode-border: rgba(6, 182, 212, 0.15);
    --et-progress-mode-bg: #1e293b;
    --et-progress-mode-text: #94a3b8;
    --et-progress-mode-active-border: #06b6d4;
    --et-progress-mode-active-bg: #06b6d4;
    --et-progress-mode-active-text: #0f172a;
    --et-progress-status: #f1f5f9;

    /* Semantic colors */
    --et-error: #f87171;
    --et-success: #34d399;
    --et-warning: #fbbf24;
    --et-mark-bg: rgba(6, 182, 212, 0.2);
}
```

- [ ] **Step 2: Visually check dark mode**

Open `http://127.0.0.1:35231/`, toggle to dark mode. Verify:
- Page background is deep slate (#0f172a)
- Card surfaces are #1e293b with faint cyan-tinted borders
- Accent elements are cyan (#06b6d4)
- No white/light artifacts remaining from old theme
- Text contrast is comfortable to read

- [ ] **Step 3: Commit**

```bash
git add app/static/styles.css
git commit -m "feat: rewrite dark mode CSS variables to cyan-tinted developer aesthetic"
```

---

### Task 4: Typography and body styles

**Files:**
- Modify: `app/static/styles.css:100-106` (the `body` rule)

- [ ] **Step 1: Update body styles with Inter font and Bootstrap variable overrides**

In `app/static/styles.css`, replace the `body` rule (lines 100-105) with:

```css
body {
    font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    font-size: 0.875rem;
    line-height: 1.6;
    color: var(--et-text);
    background:
        radial-gradient(circle at top left, var(--et-bg-gradient-accent), transparent 32%),
        linear-gradient(180deg, var(--et-bg-gradient-start) 0%, var(--et-bg-gradient-end) 100%);
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}
```

- [ ] **Step 2: Add Bootstrap variable overrides and utility classes after the body rule**

Insert immediately after the `body` rule:

```css
/* Bootstrap variable overrides */
:root,
[data-bs-theme="light"] {
    --bs-body-bg: #f8fafc;
    --bs-body-color: #0f172a;
    --bs-primary: #0891b2;
    --bs-primary-rgb: 8, 145, 178;
    --bs-link-color: #0891b2;
    --bs-link-hover-color: #0e7490;
}

[data-bs-theme="dark"] {
    --bs-body-bg: #0f172a;
    --bs-body-color: #f1f5f9;
    --bs-primary: #06b6d4;
    --bs-primary-rgb: 6, 182, 212;
    --bs-link-color: #06b6d4;
    --bs-link-hover-color: #22d3ee;
}

/* Monospace utility for dates, tags, badges */
.font-mono {
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* Typography scale */
h1, .h1 { font-size: clamp(1.3rem, 2vw, 1.5rem); font-weight: 700; line-height: 1.3; }
h2, .h2 { font-size: 1.15rem; font-weight: 600; line-height: 1.3; }
h3, .h3 { font-size: 1rem; font-weight: 600; line-height: 1.3; }

/* Navbar overrides */
.navbar {
    background: var(--et-bg-gradient-start) !important;
    border-bottom: 1px solid var(--et-border) !important;
}

.navbar-brand {
    font-weight: 700;
    color: var(--et-text) !important;
    letter-spacing: -0.01em;
}

/* Card overrides — remove Bootstrap defaults, apply subtle-border style */
.card {
    background: var(--et-card-bg);
    border: 1px solid var(--et-border);
    border-radius: 8px;
    box-shadow: none;
}

[data-bs-theme="light"] .card {
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

/* Button overrides */
.btn-primary {
    --bs-btn-bg: var(--et-primary);
    --bs-btn-border-color: var(--et-primary);
    --bs-btn-hover-bg: var(--et-primary);
    --bs-btn-hover-border-color: var(--et-primary);
    --bs-btn-color: #0f172a;
    --bs-btn-hover-color: #0f172a;
    --bs-btn-active-bg: var(--et-primary);
    --bs-btn-active-border-color: var(--et-primary);
    --bs-btn-active-color: #0f172a;
    font-weight: 600;
    filter: brightness(1);
    transition: filter 0.15s ease, transform 0.15s ease;
}

.btn-primary:hover {
    filter: brightness(1.1);
    transform: translateY(-1px);
}

.btn-outline-secondary {
    --bs-btn-color: var(--et-text-secondary);
    --bs-btn-border-color: var(--et-border);
    --bs-btn-hover-bg: var(--et-surface-bg-subtle);
    --bs-btn-hover-border-color: var(--et-primary-border);
    --bs-btn-hover-color: var(--et-text);
    --bs-btn-active-bg: var(--et-primary-bg);
    --bs-btn-active-border-color: var(--et-primary-border);
    --bs-btn-active-color: var(--et-primary);
    border-radius: 6px;
}

/* Form control overrides */
.form-control,
.form-select {
    background: var(--et-surface-bg);
    border: 1px solid var(--et-border-input);
    color: var(--et-text);
    border-radius: 6px;
}

.form-control:focus,
.form-select:focus {
    border-color: var(--et-primary);
    box-shadow: 0 0 0 3px var(--et-primary-bg);
    background: var(--et-surface-bg);
    color: var(--et-text);
}

.form-control::placeholder {
    color: var(--et-text-muted);
}

/* Badge overrides — developer style with mono font */
.badge {
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.65rem;
    font-weight: 500;
    letter-spacing: 0.02em;
    border-radius: 4px;
    padding: 0.2rem 0.5rem;
}

.badge.text-bg-light {
    background: var(--et-primary-bg) !important;
    color: var(--et-primary) !important;
    border-color: var(--et-primary-border) !important;
}

/* Uppercase labels */
.timeline-web-card-label,
.group-flyout-label,
.entry-live-preview-label,
.story-kicker,
.story-guide-label,
.story-meta-label,
.story-preview-title,
.visualization-toolbar-label,
.visualization-summary-label,
.timeline-web-progress-label,
.story-progress-label {
    font-family: 'Inter', system-ui, sans-serif;
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--et-text-muted);
}

/* Date elements — monospace accent */
.visualization-date,
.text-body-secondary.small {
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.7rem;
    font-weight: 400;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--et-text-date);
}

/* Progress log — use JetBrains Mono */
.timeline-web-progress-log {
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
```

- [ ] **Step 3: Verify typography renders correctly**

Open `http://127.0.0.1:35231/`. Verify in both light and dark modes:
- Body text renders in Inter
- Dates, badges, and tags render in JetBrains Mono
- Navbar brand is bold Inter
- Buttons show cyan primary color
- Form inputs have correct border and focus colors
- Cards show subtle borders (no heavy shadows)

- [ ] **Step 4: Commit**

```bash
git add app/static/styles.css
git commit -m "feat: add Inter/JetBrains Mono typography, Bootstrap overrides, and component restyling"
```

---

### Task 5: Update entry card partial for monospace dates

**Files:**
- Modify: `app/templates/partials/entry_card.html:6`

- [ ] **Step 1: Add font-mono class to the date span**

In `app/templates/partials/entry_card.html`, replace line 6:

```html
                    <span>{{ entry.display_date }}</span>
```

With:

```html
                    <span class="font-mono">{{ entry.display_date }}</span>
```

- [ ] **Step 2: Verify entry cards show monospace dates**

Open `http://127.0.0.1:35231/` and check that entry card dates render in JetBrains Mono while the rest of the card text remains in Inter.

- [ ] **Step 3: Commit**

```bash
git add app/templates/partials/entry_card.html
git commit -m "feat: use monospace font for entry card dates"
```

---

### Task 6: Update navbar markup for cleaner developer style

**Files:**
- Modify: `app/templates/base.html:20-44`

- [ ] **Step 1: Update navbar classes**

In `app/templates/base.html`, replace line 20:

```html
    <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom sticky-top">
```

With:

```html
    <nav class="navbar navbar-expand-lg sticky-top">
```

The `bg-body-tertiary` and `border-bottom` Bootstrap classes are no longer needed because the new CSS rules in Task 4 handle navbar background and border via the `.navbar` selector.

- [ ] **Step 2: Verify navbar appearance**

Open `http://127.0.0.1:35231/` in both light and dark modes. Verify:
- Navbar background matches the page background (dark slate in dark mode, light in light mode)
- Subtle bottom border is visible
- All navbar elements (brand, search, buttons, toggle) are visible and functional
- "New Entry" button is cyan

- [ ] **Step 3: Commit**

```bash
git add app/templates/base.html
git commit -m "feat: simplify navbar classes, styling now handled by CSS overrides"
```

---

### Task 7: Run E2E tests to verify no regressions

**Files:**
- No file changes — verification only

- [ ] **Step 1: Run Python E2E tests**

Run: `uv run pytest tests/e2e -v`

Expected: All tests pass. The redesign is CSS-only with minimal HTML class changes, so selectors used in tests should still work.

- [ ] **Step 2: Run TypeScript E2E tests**

Run: `npm run test:e2e:ts`

Expected: All tests pass.

- [ ] **Step 3: Run Python unit/integration tests**

Run: `uv run pytest tests/ -v --ignore=tests/e2e`

Expected: All tests pass (no backend changes were made).

- [ ] **Step 4: If any test fails, diagnose and fix**

If a test fails due to a changed class name or selector:
- Read the failing test to identify what selector it uses
- Check if the selector still exists in the updated HTML
- Fix the CSS or HTML to maintain the selector, or update the test if the selector was intentionally changed

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve E2E test regressions from redesign"
```

Only run this step if fixes were needed.

---

### Task 8: Final visual QA across all pages

**Files:**
- No file changes — verification only

- [ ] **Step 1: Check timeline page**

Open `http://127.0.0.1:35231/`. Verify in both dark and light modes:
- Timeline cards have subtle borders, cyan accents
- Group flyout dropdown works and matches the new palette
- Filter chips show cyan active state
- Visualization timeline has cyan connecting line and nodes
- Summary cards match new style

- [ ] **Step 2: Check entry form**

Open `http://127.0.0.1:35231/entries/new`. Verify:
- Form inputs have correct background, border, and focus ring colors
- Labels and help text use correct colors
- Submit button is cyan
- Live preview section matches

- [ ] **Step 3: Check entry detail and search**

Navigate to an entry detail page and the search page. Verify:
- Entry detail content renders correctly
- Search results show monospace dates and cyan accents
- Search highlight marks use the new mark background

- [ ] **Step 4: Check story mode and admin pages**

Navigate to `/story` and `/admin/groups`. Verify:
- Story hero, sidebar, output, and citation components match
- Admin page tables and forms use correct styling
- All pages are readable and consistent

- [ ] **Step 5: Check dark mode toggle**

Toggle between dark and light modes on each page. Verify:
- Transition is smooth
- No flash of unstyled content
- Both modes look intentional and polished
