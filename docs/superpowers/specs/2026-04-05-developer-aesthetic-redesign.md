# EventTracker Developer Aesthetic Redesign

**Date:** 2026-04-05
**Status:** Approved
**Scope:** Full frontend visual redesign — CSS-first, minimal template changes, no backend changes

## Overview

Redesign EventTracker's frontend from generic Bootstrap to a distinctive **developer/technical aesthetic** inspired by Linear, Raycast, and terminal-based tools. The redesign is CSS-first: ~80% of changes live in `styles.css` variable remapping and component restyling, with minimal HTML template adjustments.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Direction | Developer / Technical | Dark-first, information-dense, monospace accents, sharp corners |
| Dark/Light | Both equally polished | Current toggle stays, both modes get full treatment |
| Accent color | Cyan/Teal (`#06b6d4`) | Terminal-inspired, distinctive, high contrast in both modes |
| Typography | Inter (body) + JetBrains Mono (accents) | Modern legibility + dev flavor for metadata |
| Component style | Subtle Border | Flat surfaces, faint cyan-tinted borders, no glow/glass effects |
| Navigation | Modernized top navbar | Least disruptive, existing layout preserved |

## Color System

### Dark Mode (Primary)

| Token | Value | Usage |
|---|---|---|
| `--bg-primary` | `#0f172a` (slate-900) | Page background |
| `--bg-surface` | `#1e293b` (slate-800) | Cards, panels, inputs |
| `--bg-surface-hover` | `#334155` (slate-700) | Hover states on surfaces |
| `--border-default` | `rgba(6, 182, 212, 0.15)` | Card borders, dividers |
| `--border-hover` | `rgba(6, 182, 212, 0.3)` | Borders on hover |
| `--accent` | `#06b6d4` (cyan-500) | Primary interactive color |
| `--accent-hover` | `#22d3ee` (cyan-400) | Hover state for accent |
| `--accent-muted` | `rgba(6, 182, 212, 0.1)` | Tinted backgrounds for badges |
| `--text-primary` | `#f1f5f9` (slate-100) | Headings, primary text |
| `--text-secondary` | `#94a3b8` (slate-400) | Body text, descriptions |
| `--text-muted` | `#64748b` (slate-500) | Placeholders, help text |
| `--success` | `#34d399` (emerald-400) | Success badges, positive states |
| `--warning` | `#fbbf24` (amber-400) | Warning badges |
| `--error` | `#f87171` (red-400) | Error states, destructive actions |

### Light Mode

| Token | Value | Usage |
|---|---|---|
| `--bg-primary` | `#f8fafc` (slate-50) | Page background |
| `--bg-surface` | `#ffffff` | Cards, panels, inputs |
| `--bg-surface-hover` | `#f1f5f9` (slate-100) | Hover states |
| `--border-default` | `rgba(15, 23, 42, 0.1)` | Card borders, dividers |
| `--border-hover` | `rgba(15, 23, 42, 0.2)` | Borders on hover |
| `--accent` | `#0891b2` (cyan-600) | Primary interactive color |
| `--accent-hover` | `#0e7490` (cyan-700) | Hover state |
| `--accent-muted` | `rgba(8, 145, 178, 0.08)` | Tinted backgrounds |
| `--text-primary` | `#0f172a` (slate-900) | Headings, primary text |
| `--text-secondary` | `#475569` (slate-600) | Body text |
| `--text-muted` | `#94a3b8` (slate-400) | Placeholders, help text |
| `--success` | `#059669` (emerald-600) | Success states |
| `--warning` | `#d97706` (amber-600) | Warning states |
| `--error` | `#dc2626` (red-600) | Error states |

## Typography

### Fonts

- **Inter** (variable, Google Fonts CDN) — all body text, headings, UI labels
- **JetBrains Mono** (Google Fonts CDN) — dates, tags, badges, code snippets, metadata

### Scale

| Element | Size | Weight | Font |
|---|---|---|---|
| Page title (h1) | `clamp(1.3rem, 2vw, 1.5rem)` | 700 | Inter |
| Section heading (h2) | `1.15rem` | 600 | Inter |
| Card title | `0.95rem` | 600 | Inter |
| Body text | `0.875rem` | 400 | Inter |
| Secondary text | `0.8rem` | 400 | Inter |
| Labels (uppercase) | `0.65rem` | 500 | Inter, `letter-spacing: 0.1em` |
| Dates | `0.7rem` | 400 | JetBrains Mono |
| Badges/Tags | `0.65rem` | 500 | JetBrains Mono |

### Line Heights

- Headings: 1.3
- Body: 1.6
- Compact (badges, labels): 1.2

## Component Specifications

### Cards

```
Background:   var(--bg-surface)
Border:       1px solid var(--border-default)
Radius:       8px
Padding:      1.25rem
Shadow:       none (dark), 0 1px 3px rgba(0,0,0,0.05) (light)
Hover:        border-color transitions to var(--border-hover), translateY(-1px)
Transition:   all 0.15s ease
```

### Buttons

**Primary:**
```
Background:   var(--accent)
Color:        #0f172a (always dark text on cyan)
Border:       none
Radius:       6px
Padding:      0.5rem 1rem
Hover:        var(--accent-hover), slight brightness increase
```

**Secondary/Ghost:**
```
Background:   transparent
Color:        var(--text-secondary)
Border:       1px solid var(--border-default)
Radius:       6px
Hover:        background var(--bg-surface-hover), border var(--border-hover)
```

**Danger:**
```
Background:   transparent
Color:        var(--error)
Border:       1px solid currentColor at 30% opacity
Hover:        background with error at 10% opacity
```

### Badges/Tags

```
Font:         JetBrains Mono, 0.65rem, weight 500
Background:   var(--accent-muted) or semantic color at 10% opacity
Color:        var(--accent) or semantic color
Border:       1px solid current color at 20% opacity
Radius:       4px
Padding:      0.1rem 0.5rem
```

Semantic variants: default (cyan), success (green), warning (amber), error (red).

### Form Inputs

```
Background:   var(--bg-surface)
Border:       1px solid var(--border-default)
Color:        var(--text-primary)
Radius:       6px
Padding:      0.5rem 0.75rem
Focus:        border-color var(--accent), box-shadow 0 0 0 3px var(--accent-muted)
Placeholder:  var(--text-muted)
```

### Navbar

```
Background:   var(--bg-primary) with subtle bottom border
Border-bottom: 1px solid var(--border-default)
Position:     sticky top
Height:       ~56px
Logo/Brand:   var(--text-primary), font-weight 700
Nav links:    var(--text-secondary), hover var(--text-primary)
Search input: var(--bg-surface) with border
CTA button:   Primary button style (cyan)
Dark toggle:  Icon button, ghost style
```

### Timeline Visualization

```
Connecting line:  2px solid var(--accent) at 20% opacity
Node dots:        10px circle, var(--accent) fill, 2px border
Node hover:       slight scale(1.2) + brightness
Entry cards:      Standard card component
Date labels:      JetBrains Mono, var(--accent), uppercase
Group headers:    Inter 600, var(--text-primary)
```

## Files to Modify

### Primary (CSS-first changes)

1. **`app/static/styles.css`** — Full rewrite of:
   - CSS custom property definitions (both `:root` and `[data-bs-theme="dark"]`)
   - Card component styles
   - Button overrides
   - Badge/tag styles
   - Form input styles
   - Navbar styles
   - Timeline visualization styles
   - Typography (font-family, sizes, weights)
   - Background gradients and page-level styling

2. **`app/templates/base.html`** — Add font imports:
   - Inter variable font from Google Fonts
   - JetBrains Mono from Google Fonts
   - Minor navbar markup cleanup if needed for class changes

### Secondary (minor template adjustments)

3. **`app/templates/partials/entry_card.html`** — Add monospace class to date elements if not CSS-targetable
4. **`app/templates/timeline.html`** — Adjust timeline node/line classes if CSS selectors need updating
5. **Other templates** — Minimal changes; most styling is driven by CSS variables and Bootstrap utility classes that remain unchanged

### No Changes

- No JavaScript changes
- No backend/Python changes
- No database changes
- No test changes (visual only, all selectors and functionality preserved)

## Bootstrap Integration

The redesign works **on top of Bootstrap 5.3**, not against it:

- Override Bootstrap's CSS variables (`--bs-body-bg`, `--bs-body-color`, `--bs-primary`, etc.) to match the new palette
- Override component-specific variables where Bootstrap exposes them (`--bs-card-*`, `--bs-btn-*`, etc.)
- Use Bootstrap's `data-bs-theme` attribute for dark/light switching (existing mechanism)
- Keep all Bootstrap utility classes working (`text-*`, `bg-*`, `border-*`, etc.)
- Custom styles layer on top via specificity, not `!important`

## Success Criteria

- Both dark and light modes look polished and intentional
- The app no longer reads as "generic Bootstrap"
- All existing functionality works identically
- All existing E2E tests pass without modification
- Page load performance is not degraded (fonts loaded via `display=swap`)
- Accessibility contrast ratios maintained (WCAG AA minimum)
