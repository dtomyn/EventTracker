# Entry Form UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the entry create/edit form into logical sections with better proximity between related actions (URL + Generate), collapsed optional sections, and smoother interactivity.

**Architecture:** Pure frontend change — restructure `entry_form.html` template into three visual sections (Source & Generation, Entry Details, Content), add CSS for section styling and field-highlight animations, and update JS for collapse toggles, auto-scroll after generation, and Ctrl+Enter shortcut. No backend route or model changes. All existing form field `name` attributes, IDs, and `data-*` attributes preserved to maintain E2E test compatibility.

**Tech Stack:** Jinja2 templates, Bootstrap 5.3, vanilla JavaScript, custom CSS variables from existing design system.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `app/templates/entry_form.html` | Modify (full restructure) | Template layout: regroup fields into 3 sections, add collapse toggles, update JS |
| `app/static/styles.css` | Modify (append ~80 lines) | Section divider styling, highlight animation, generate button accent |
| `app/templates/partials/generated_preview.html` | No change | Hidden fields and suggestion display — unchanged |
| `tests/e2e/test_core_workflows.py` | Potentially minor | Only if collapse changes break existing flows (unlikely — fields stay in DOM) |
| `tstests/e2e/poms/entry-form-page.ts` | No change | All locators use labels/roles/IDs which are preserved |

### Selector Preservation Contract

These selectors are used in E2E tests and **must not change**:

- **Labels:** `Timeline Group`, `Year`, `Month`, `Day`, `Title`, `Source URL`, `Event Summary`, `Tags`, `URL`, `Brief Note`
- **Button names:** `Add Link`, `Save Entry`, `Generate`, `Remove link row`
- **IDs:** `group_id`, `event_year`, `event_month`, `event_day`, `title`, `source_url`, `final_text`, `tags`, `link_url_N`, `link_note_N`, `generated_text`, `generate-feedback`, `generated_suggested_*`, `final-text-preview`, `generate-button`, `add-link-row`, `entry-links-container`
- **Data attributes:** `data-link-row`, `data-remove-link-row`, `data-html-preview-source`, `data-preview-target`

---

## Task 1: Add CSS for form sections, generate button accent, and highlight animation

**Files:**
- Modify: `app/static/styles.css:1639` (append after last line)

- [ ] **Step 1: Append new CSS rules to styles.css**

Add these rules at the end of `app/static/styles.css`, after line 1639:

```css
/* === Entry Form Section Layout === */

.entry-form-section {
    padding: 1.5rem 0;
}

.entry-form-section + .entry-form-section {
    border-top: 1px dashed var(--et-border-dashed);
}

.entry-form-section-label {
    color: var(--et-text-muted);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 1rem;
}

/* Generate button — accent styling to stand out */
.btn-generate {
    --bs-btn-color: var(--et-primary);
    --bs-btn-border-color: var(--et-primary-border);
    --bs-btn-hover-color: #fff;
    --bs-btn-hover-bg: var(--et-primary);
    --bs-btn-hover-border-color: var(--et-primary);
    --bs-btn-active-color: #fff;
    --bs-btn-active-bg: var(--et-primary);
    --bs-btn-active-border-color: var(--et-primary);
    --bs-btn-focus-shadow-rgb: var(--bs-primary-rgb);
    background: var(--et-primary-bg);
    border-radius: 6px;
    font-weight: 500;
    gap: 0.4rem;
    display: inline-flex;
    align-items: center;
}

.btn-generate:disabled {
    opacity: 0.65;
    pointer-events: none;
}

.btn-generate .spinner-border {
    width: 0.85rem;
    height: 0.85rem;
    border-width: 0.15em;
}

/* Collapse toggle links for optional sections */
.entry-form-collapse-toggle {
    color: var(--et-text-muted);
    font-size: 0.8rem;
    text-decoration: none;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    transition: color 0.15s ease;
}

.entry-form-collapse-toggle:hover {
    color: var(--et-primary);
}

.entry-form-collapse-toggle .collapse-chevron {
    display: inline-block;
    transition: transform 0.2s ease;
    font-size: 0.65rem;
}

.entry-form-collapse-toggle[aria-expanded="true"] .collapse-chevron {
    transform: rotate(90deg);
}

/* Field highlight animation after AI generation fills values */
@keyframes field-highlight-fade {
    0% { box-shadow: 0 0 0 3px var(--et-primary-focus-shadow); }
    100% { box-shadow: none; }
}

.field-just-filled {
    animation: field-highlight-fade 1.5s ease-out forwards;
}

/* Generate helper text below the button */
.generate-hint {
    color: var(--et-text-muted);
    font-size: 0.75rem;
    margin-top: 0.35rem;
}
```

- [ ] **Step 2: Verify CSS loads without errors**

Run: `uv run python -m scripts.run_dev --reload`

Open `http://127.0.0.1:35231/entries/new` in a browser. Confirm the page loads without CSS errors in the browser console. The new classes aren't applied yet so visually nothing changes.

- [ ] **Step 3: Commit**

```bash
git add app/static/styles.css
git commit -m "style: add CSS for entry form sections, generate accent, and field highlight animation"
```

---

## Task 2: Restructure the entry form template into three sections

This is the core layout change. The form fields are regrouped but keep all existing `id`, `name`, `for`, and `data-*` attributes identical.

**Files:**
- Modify: `app/templates/entry_form.html:1-131` (template portion, before `<script>`)

**New layout inside `<div class="row g-3">`:**

**Section A — Source & Generation** (top of form, the AI workflow starting point)
1. Source URL
2. Summary Instructions (collapsed by default, visible if value present in edit mode)
3. Generate button (prominent, with hint text)
4. Generated preview container

**Section B — Entry Details** (metadata, auto-filled after generation)
5. Timeline Group
6. Year / Month / Day
7. Title
8. Tags

**Section C — Content** (the main entry body + optional links)
9. Event Summary textarea + live preview
10. Additional Links (collapsed by default, visible if links present in edit mode)

- [ ] **Step 1: Rewrite the form body in entry_form.html**

Replace everything between `<div class="card-body p-4 p-lg-5">` and the closing `</div>` of card-body (lines 16-123) with:

```html
<div class="card-body p-4 p-lg-5">
    <!-- Section A: Source & Generation -->
    <div class="entry-form-section" style="padding-top: 0;">
        <div class="entry-form-section-label">Source & Generation</div>
        <div class="row g-3">
            <div class="col-12">
                <label class="form-label" for="source_url">Source URL</label>
                <input class="form-control {% if form_state.errors.get('source_url') %}is-invalid{% endif %}" id="source_url" name="source_url" value="{{ form_state.values.get('source_url', '') }}" placeholder="https://example.com/article">
                {% if form_state.errors.get('source_url') %}<div class="invalid-feedback">{{ form_state.errors['source_url'] }}</div>{% endif %}
                <div class="form-text">Only the URL is stored. Extracted article content is kept in memory only during generation.</div>
            </div>

            <div class="col-12">
                <a class="entry-form-collapse-toggle" data-bs-toggle="collapse" href="#summary-instructions-collapse" role="button" aria-expanded="{{ 'true' if form_state.values.get('summary_instructions', '') else 'false' }}" aria-controls="summary-instructions-collapse">
                    <span class="collapse-chevron">&#9654;</span> Generation instructions
                </a>
                <div class="collapse {{ 'show' if form_state.values.get('summary_instructions', '') }}" id="summary-instructions-collapse">
                    <div class="mt-2">
                        <label class="form-label" for="summary_instructions">Summary Instructions</label>
                        <textarea class="form-control" id="summary_instructions" name="summary_instructions" rows="3" maxlength="1000" placeholder="Optional guidance for AI summarization, for example: focus on release milestones and omit marketing language.">{{ form_state.values.get('summary_instructions', '') }}</textarea>
                        <div class="form-text">Optional. Used only when generating from the source URL or current input, and never stored with the entry.</div>
                    </div>
                </div>
            </div>

            <div class="col-12">
                <button class="btn btn-generate" type="button" id="generate-button">
                    <span id="generate-button-label">Generate</span>
                    <span id="generate-spinner" class="spinner-border d-none" role="status" aria-hidden="true"></span>
                </button>
                <div class="generate-hint">Fetches the article and drafts a summary, title, date, and tags.</div>
                <div id="generated-preview-container" class="mt-2">
                    {% include "partials/generated_preview.html" %}
                </div>
            </div>
        </div>
    </div>

    <!-- Section B: Entry Details -->
    <div class="entry-form-section">
        <div class="entry-form-section-label">Entry Details</div>
        <div class="row g-3">
            <div class="col-12">
                <label class="form-label" for="group_id">Timeline Group</label>
                <select class="form-select {% if form_state.errors.get('group_id') %}is-invalid{% endif %}" id="group_id" name="group_id">
                    <option value="">Select a group</option>
                    {% for timeline_filter in timeline_filters %}
                        <option value="{{ timeline_filter.id }}" {% if form_state.values.get('group_id') == (timeline_filter.id | string) %}selected{% endif %}>{{ timeline_filter.name }}</option>
                    {% endfor %}
                </select>
                {% if form_state.errors.get('group_id') %}<div class="invalid-feedback">{{ form_state.errors['group_id'] }}</div>{% endif %}
            </div>

            <div class="col-md-4">
                <label class="form-label" for="event_year">Year</label>
                <input class="form-control {% if form_state.errors.get('event_year') %}is-invalid{% endif %}" id="event_year" name="event_year" value="{{ form_state.values.get('event_year', '') }}" inputmode="numeric">
                {% if form_state.errors.get('event_year') %}<div class="invalid-feedback">{{ form_state.errors['event_year'] }}</div>{% endif %}
            </div>
            <div class="col-md-4">
                <label class="form-label" for="event_month">Month</label>
                <input class="form-control {% if form_state.errors.get('event_month') %}is-invalid{% endif %}" id="event_month" name="event_month" value="{{ form_state.values.get('event_month', '') }}" inputmode="numeric">
                {% if form_state.errors.get('event_month') %}<div class="invalid-feedback">{{ form_state.errors['event_month'] }}</div>{% endif %}
            </div>
            <div class="col-md-4">
                <label class="form-label" for="event_day">Day</label>
                <input class="form-control {% if form_state.errors.get('event_day') %}is-invalid{% endif %}" id="event_day" name="event_day" value="{{ form_state.values.get('event_day', '') }}" inputmode="numeric">
                {% if form_state.errors.get('event_day') %}<div class="invalid-feedback">{{ form_state.errors['event_day'] }}</div>{% endif %}
            </div>

            <div class="col-12">
                <label class="form-label" for="title">Title</label>
                <input class="form-control {% if form_state.errors.get('title') %}is-invalid{% endif %}" id="title" name="title" value="{{ form_state.values.get('title', '') }}">
                {% if form_state.errors.get('title') %}<div class="invalid-feedback">{{ form_state.errors['title'] }}</div>{% endif %}
            </div>

            <div class="col-12">
                <label class="form-label" for="tags">Tags</label>
                <input class="form-control" id="tags" name="tags" value="{{ form_state.values.get('tags', '') }}" placeholder="research, milestone, release">
                <div class="form-text">Comma-separated. Tags are normalized and deduplicated on save.</div>
            </div>
        </div>
    </div>

    <!-- Section C: Content -->
    <div class="entry-form-section">
        <div class="entry-form-section-label">Content</div>
        <div class="row g-3">
            <div class="col-12">
                <label class="form-label" for="final_text">Event Summary</label>
                <textarea class="form-control {% if form_state.errors.get('final_text') %}is-invalid{% endif %}" id="final_text" name="final_text" rows="8" data-html-preview-source data-preview-target="final-text-preview">{{ form_state.values.get('final_text', '') }}</textarea>
                {% if form_state.errors.get('final_text') %}<div class="invalid-feedback">{{ form_state.errors['final_text'] }}</div>{% endif %}
                <div class="form-text">Supports basic HTML such as &lt;b&gt;, &lt;i&gt;, &lt;ul&gt;, &lt;ol&gt;, and &lt;li&gt;.</div>
                <div class="entry-live-preview mt-3">
                    <div class="entry-live-preview-label">Live preview</div>
                    <div class="entry-live-preview-body" id="final-text-preview" aria-live="polite">
                        {% set preview_html = form_state.values.get('final_text', '') | render_entry_html %}
                        {% set empty_message = 'Event summary preview updates here as you type.' %}
                        {% include "partials/html_preview_content.html" %}
                    </div>
                </div>
            </div>

            <div class="col-12">
                {% set has_links = form_state.link_rows | length > 0 and (form_state.link_rows[0].url or form_state.link_rows[0].note) %}
                <div class="d-flex justify-content-between align-items-center gap-3 mb-2">
                    <a class="entry-form-collapse-toggle" data-bs-toggle="collapse" href="#additional-links-collapse" role="button" aria-expanded="{{ 'true' if has_links else 'false' }}" aria-controls="additional-links-collapse">
                        <span class="collapse-chevron">&#9654;</span> Additional Links
                    </a>
                    <button class="btn btn-outline-secondary btn-sm" type="button" id="add-link-row">Add Link</button>
                </div>
                <div class="form-text mb-2">Each extra URL requires a short note explaining why it was added.</div>
                <div class="collapse {{ 'show' if has_links }}" id="additional-links-collapse">
                    <div class="vstack gap-3" id="entry-links-container">
                        {% for link_row in form_state.link_rows %}
                            {% set row_index = loop.index0 %}
                            <div class="border rounded p-3" data-link-row>
                                <div class="row g-3 align-items-start">
                                    <div class="col-md-6">
                                        <label class="form-label" for="link_url_{{ row_index }}">URL</label>
                                        <input class="form-control {% if form_state.errors.get('link_url_' ~ row_index) %}is-invalid{% endif %}" id="link_url_{{ row_index }}" name="link_url" value="{{ link_row.url }}" placeholder="https://example.com/reference">
                                        {% if form_state.errors.get('link_url_' ~ row_index) %}<div class="invalid-feedback">{{ form_state.errors['link_url_' ~ row_index] }}</div>{% endif %}
                                    </div>
                                    <div class="col-md-5">
                                        <label class="form-label" for="link_note_{{ row_index }}">Brief Note</label>
                                        <input class="form-control {% if form_state.errors.get('link_note_' ~ row_index) %}is-invalid{% endif %}" id="link_note_{{ row_index }}" name="link_note" value="{{ link_row.note }}" placeholder="Why this link matters">
                                        {% if form_state.errors.get('link_note_' ~ row_index) %}<div class="invalid-feedback">{{ form_state.errors['link_note_' ~ row_index] }}</div>{% endif %}
                                    </div>
                                    <div class="col-md-1 d-flex align-items-end">
                                        <button class="btn btn-outline-danger w-100" type="button" data-remove-link-row aria-label="Remove link row">x</button>
                                    </div>
                                </div>
                            </div>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
```

Key changes from the original:
- **Source URL** moved from position 4 to position 1 (top of form)
- **Generate button** now directly follows Source URL instead of being buried at Event Summary label
- **Summary Instructions** collapsed by default behind a toggle (auto-expands if value present in edit mode)
- **Additional Links** collapsed by default behind a toggle (auto-expands if links exist in edit mode)
- **Tags** moved from after Event Summary to after Title in the "Entry Details" section
- Three sections separated by dashed borders via `.entry-form-section`
- Generate button uses new `.btn-generate` class for accent styling
- Generate button includes a spinner element (hidden by default) for loading state
- All field `id`, `name`, and `data-*` attributes are **identical** to the original

- [ ] **Step 2: Verify template renders**

Run: `uv run python -m scripts.run_dev --reload`

Open `http://127.0.0.1:35231/entries/new` in a browser. Verify:
- Three sections visible with dashed dividers between them
- Source URL is the first field
- Generate button is directly below Source URL area
- "Generation instructions" toggle link collapses/expands Summary Instructions
- "Additional Links" toggle link collapses/expands the links section
- All fields render and are interactive

- [ ] **Step 3: Commit**

```bash
git add app/templates/entry_form.html
git commit -m "feat: reorganize entry form into Source/Details/Content sections with collapsed optional fields"
```

---

## Task 3: Update JavaScript for loading state, field highlights, auto-scroll, and keyboard shortcut

**Files:**
- Modify: `app/templates/entry_form.html` (the `<script>` block, lines 132-348)

- [ ] **Step 1: Replace the `<script>` block in entry_form.html**

Replace the entire `<script>...</script>` block (from `<script>` to `</script>` before `{% endblock %}`) with:

```html
<script>
    const generateButton = document.getElementById('generate-button');
    const generateButtonLabel = document.getElementById('generate-button-label');
    const generateSpinner = document.getElementById('generate-spinner');
    const previewContainer = document.getElementById('generated-preview-container');
    const linkContainer = document.getElementById('entry-links-container');
    const addLinkRowButton = document.getElementById('add-link-row');
    const finalTextField = document.getElementById('final_text');
    const sourceUrlField = document.getElementById('source_url');
    const htmlPreviewEndpoint = '/entries/preview-html';
    const htmlPreviewTimers = new WeakMap();
    const htmlPreviewControllers = new WeakMap();

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function renderGenerationFeedback(feedbackClass = 'text-body-secondary', feedbackMessage = '') {
        return `
            <input type="hidden" id="generated_text" name="generated_text" value="">
            <div id="generate-feedback" class="${feedbackClass}">${feedbackMessage}</div>
            <input type="hidden" id="generated_suggested_title" value="">
            <input type="hidden" id="generated_suggested_event_year" value="">
            <input type="hidden" id="generated_suggested_event_month" value="">
            <input type="hidden" id="generated_suggested_event_day" value="">
            <input type="hidden" id="generated_suggested_tags" value="">
        `;
    }

    function setGenerateLoading(isLoading) {
        generateButton.disabled = isLoading;
        generateButtonLabel.textContent = isLoading ? 'Generating\u2026' : 'Generate';
        generateSpinner.classList.toggle('d-none', !isLoading);
    }

    function highlightField(fieldId) {
        const field = document.getElementById(fieldId);
        if (!field) return;
        field.classList.remove('field-just-filled');
        void field.offsetWidth; // force reflow to restart animation
        field.classList.add('field-just-filled');
    }

    function scheduleHtmlPreview(field, delay = 160) {
        const existingTimer = htmlPreviewTimers.get(field);
        if (existingTimer) {
            window.clearTimeout(existingTimer);
        }

        const timerId = window.setTimeout(() => {
            htmlPreviewTimers.delete(field);
            void refreshHtmlPreview(field);
        }, delay);
        htmlPreviewTimers.set(field, timerId);
    }

    async function refreshHtmlPreview(field) {
        const previewTargetId = field.dataset.previewTarget;
        const previewTarget = previewTargetId ? document.getElementById(previewTargetId) : null;

        if (!previewTarget) {
            return;
        }

        const existingController = htmlPreviewControllers.get(field);
        if (existingController) {
            existingController.abort();
        }

        const controller = new AbortController();
        htmlPreviewControllers.set(field, controller);

        const payload = new FormData();
        payload.append('raw_html', field.value);
        payload.append('csrf_token', getCsrfToken());

        try {
            const response = await fetch(htmlPreviewEndpoint, {
                method: 'POST',
                body: payload,
                headers: {
                    'x-csrf-token': getCsrfToken(),
                },
                signal: controller.signal,
            });

            if (!response.ok) {
                throw new Error(`Preview request failed with ${response.status}`);
            }

            previewTarget.innerHTML = await response.text();
        } catch {
            if (controller.signal.aborted) {
                return;
            }
            previewTarget.innerHTML = '<p class="entry-live-preview-empty text-danger mb-0">Preview is temporarily unavailable.</p>';
        } finally {
            if (htmlPreviewControllers.get(field) === controller) {
                htmlPreviewControllers.delete(field);
            }
        }
    }

    function createLinkRow(url = '', note = '') {
        const rowIndex = linkContainer.querySelectorAll('[data-link-row]').length;
        const wrapper = document.createElement('div');
        wrapper.className = 'border rounded p-3';
        wrapper.setAttribute('data-link-row', '');
        wrapper.innerHTML = `
            <div class="row g-3 align-items-start">
                <div class="col-md-6">
                    <label class="form-label" for="link_url_${rowIndex}">URL</label>
                    <input class="form-control" id="link_url_${rowIndex}" name="link_url" value="${url}" placeholder="https://example.com/reference">
                </div>
                <div class="col-md-5">
                    <label class="form-label" for="link_note_${rowIndex}">Brief Note</label>
                    <input class="form-control" id="link_note_${rowIndex}" name="link_note" value="${note}" placeholder="Why this link matters">
                </div>
                <div class="col-md-1 d-flex align-items-end">
                    <button class="btn btn-outline-danger w-100" type="button" data-remove-link-row aria-label="Remove link row">x</button>
                </div>
            </div>
        `;
        return wrapper;
    }

    function ensureAtLeastOneLinkRow() {
        if (!linkContainer.querySelector('[data-link-row]')) {
            linkContainer.appendChild(createLinkRow());
        }
    }

    addLinkRowButton.addEventListener('click', () => {
        // Auto-expand the links collapse when adding a row
        const linksCollapse = document.getElementById('additional-links-collapse');
        if (linksCollapse && !linksCollapse.classList.contains('show')) {
            const bsCollapse = new bootstrap.Collapse(linksCollapse, { toggle: true });
            // Update the toggle's aria-expanded
            const toggle = document.querySelector('[aria-controls="additional-links-collapse"]');
            if (toggle) toggle.setAttribute('aria-expanded', 'true');
        }
        linkContainer.appendChild(createLinkRow());
    });

    linkContainer.addEventListener('click', (event) => {
        const button = event.target.closest('[data-remove-link-row]');
        if (!button) {
            return;
        }
        button.closest('[data-link-row]')?.remove();
        ensureAtLeastOneLinkRow();
    });

    function applySuggestedMetadata() {
        const suggestedTitle = document.getElementById('generated_suggested_title');
        const suggestedYear = document.getElementById('generated_suggested_event_year');
        const suggestedMonth = document.getElementById('generated_suggested_event_month');
        const suggestedDay = document.getElementById('generated_suggested_event_day');
        const suggestedTags = document.getElementById('generated_suggested_tags');

        const fieldsToHighlight = [];

        if (suggestedTitle && suggestedTitle.value) {
            document.getElementById('title').value = suggestedTitle.value;
            fieldsToHighlight.push('title');
        }
        if (suggestedYear && suggestedYear.value) {
            document.getElementById('event_year').value = suggestedYear.value;
            fieldsToHighlight.push('event_year');
        }
        if (suggestedMonth && suggestedMonth.value) {
            document.getElementById('event_month').value = suggestedMonth.value;
            fieldsToHighlight.push('event_month');
        }
        if (suggestedDay) {
            document.getElementById('event_day').value = suggestedDay.value;
            if (suggestedDay.value) fieldsToHighlight.push('event_day');
        }
        if (suggestedTags && suggestedTags.value) {
            document.getElementById('tags').value = suggestedTags.value;
            fieldsToHighlight.push('tags');
        }

        // Highlight auto-filled fields after a short delay so layout settles
        requestAnimationFrame(() => {
            fieldsToHighlight.forEach(id => highlightField(id));
        });
    }

    generateButton.addEventListener('click', async () => {
        const currentSummaryText = finalTextField.value;
        setGenerateLoading(true);
        previewContainer.innerHTML = renderGenerationFeedback('text-body-secondary', 'Generating summary\u2026');
        document.getElementById('generated_text').value = currentSummaryText;

        const payload = new FormData();
        payload.append('title', document.getElementById('title').value);
        payload.append('group_id', document.getElementById('group_id').value);
        payload.append('source_url', document.getElementById('source_url').value);
        const summaryInstructionsField = document.getElementById('summary_instructions');
        if (summaryInstructionsField) {
            payload.append('summary_instructions', summaryInstructionsField.value);
        }
        payload.append('generated_text', currentSummaryText);
        payload.append('csrf_token', getCsrfToken());

        try {
            const response = await fetch('/entries/generate', {
                method: 'POST',
                body: payload,
                headers: {
                    'x-csrf-token': getCsrfToken(),
                },
            });
            const responseContentType = response.headers.get('content-type') || '';
            if (!responseContentType.includes('text/html')) {
                const errorMessage = response.status === 403
                    ? 'Session expired \u2014 please reload the page and try again.'
                    : 'Generation failed. You can still write manually.';
                previewContainer.innerHTML = renderGenerationFeedback('text-danger', errorMessage);
                document.getElementById('generated_text').value = currentSummaryText;
                return;
            }
            const responseHtml = await response.text();
            previewContainer.innerHTML = responseHtml;
            if (response.ok) {
                const generatedText = document.getElementById('generated_text');
                if (generatedText) {
                    finalTextField.value = generatedText.value;
                    scheduleHtmlPreview(finalTextField, 0);
                    // Scroll to Event Summary so user sees the generated content
                    finalTextField.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    finalTextField.focus();
                }
                applySuggestedMetadata();
            }
        } catch {
            previewContainer.innerHTML = renderGenerationFeedback('text-danger', 'Summary generation request failed. You can still write manually.');
            document.getElementById('generated_text').value = currentSummaryText;
        } finally {
            setGenerateLoading(false);
        }
    });

    document.addEventListener('input', (event) => {
        if (!(event.target instanceof HTMLTextAreaElement)) {
            return;
        }
        if (!event.target.hasAttribute('data-html-preview-source')) {
            return;
        }
        scheduleHtmlPreview(event.target);
    });

    // Ctrl+Enter on Source URL triggers Generate
    sourceUrlField.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
            event.preventDefault();
            generateButton.click();
        }
    });

    // Auto-focus Source URL on new entry page
    if (!sourceUrlField.value && !document.getElementById('title').value) {
        sourceUrlField.focus();
    }

    ensureAtLeastOneLinkRow();
</script>
```

Key changes from the original script:
- **`setGenerateLoading()`** — toggles button disabled state, label text, and spinner visibility
- **`highlightField()`** — adds `.field-just-filled` class to trigger CSS animation on auto-filled fields
- **`applySuggestedMetadata()`** — now tracks which fields were filled and calls `highlightField()` on each
- **Generate click handler** — calls `setGenerateLoading(true)` at start, `setGenerateLoading(false)` in `finally`, and does `finalTextField.scrollIntoView({ behavior: 'smooth', block: 'center' })` after generation
- **Ctrl+Enter shortcut** — on `sourceUrlField` keydown, triggers Generate
- **Auto-focus** — focuses Source URL on new entry page (when title and URL are both empty)
- **Add Link button** — auto-expands the Additional Links collapse if collapsed
- All existing function signatures, DOM queries, and event patterns preserved

- [ ] **Step 2: Verify all interactivity works**

Run: `uv run python -m scripts.run_dev --reload`

Test manually:
1. Open `/entries/new` — Source URL should be auto-focused
2. Click "Generation instructions" toggle — Summary Instructions expands/collapses
3. Click "Additional Links" toggle — links section expands/collapses
4. Click "Add Link" when links are collapsed — section auto-expands and row appears
5. Type in Event Summary — live preview updates
6. Press Ctrl+Enter in Source URL field — Generate button activates
7. Verify the Generate button shows spinner and "Generating..." text (will fail without AI configured, but the loading state should appear briefly before the error)

- [ ] **Step 3: Commit**

```bash
git add app/templates/entry_form.html
git commit -m "feat: add loading state, field highlights, auto-scroll, and Ctrl+Enter shortcut to entry form"
```

---

## Task 4: Run E2E tests and fix any broken selectors

**Files:**
- Possibly modify: `tests/e2e/test_core_workflows.py`, `tests/e2e/test_optional_mocked_workflows.py`
- Possibly modify: `tstests/e2e/poms/entry-form-page.ts`

The form restructure preserves all IDs, labels, names, and data attributes. Tests should pass because:
- Label-based locators (`getByLabel('Title')`) work regardless of field order
- ID-based locators (`#final_text`, `#source_url`) are unchanged
- Role-based locators (`getByRole('button', { name: 'Generate' })`) match the same button text
- The collapsed sections (Summary Instructions, Additional Links) use Bootstrap collapse but the elements remain in the DOM — Playwright can still interact with them

However, the Additional Links collapse means `ensureAtLeastOneLinkRow()` creates a row inside a collapsed container. Tests that fill link fields may need the collapse to be opened first. The `Add Link` button auto-expands it, so tests that click "Add Link" before filling are fine. Tests that directly fill `#link_url_0` may need adjustment if the field is hidden.

- [ ] **Step 1: Run the Python E2E test suite**

Run: `uv run pytest tests/e2e/ -v --timeout=60 2>&1 | head -80`

If tests pass, skip to Step 3.

- [ ] **Step 2: Fix any failures**

The most likely failure pattern is tests trying to fill link fields that are inside a collapsed container. If a test fills `#link_url_0` directly without clicking "Add Link" first, Playwright may fail because the element is not visible.

Fix approach: In the template, ensure the `ensureAtLeastOneLinkRow()` call at the bottom of the script also expands the collapse if it creates a row. Update the function:

```javascript
function ensureAtLeastOneLinkRow() {
    if (!linkContainer.querySelector('[data-link-row]')) {
        linkContainer.appendChild(createLinkRow());
    }
}
```

This function creates a row but the collapse may hide it. Since this only runs on page load when there are no link rows (i.e., new entry mode), and the collapse is hidden by default in that case, the row exists in DOM but is visually hidden. Playwright's `fill()` works on hidden elements by default if they're in the DOM, so this should be fine. If not, we'd add logic to auto-expand when `ensureAtLeastOneLinkRow` fires.

- [ ] **Step 3: Run the TypeScript E2E test suite**

Run: `npm run test:e2e:ts 2>&1 | head -80`

If tests pass, skip to Step 5.

- [ ] **Step 4: Fix any TypeScript test failures**

Apply the same fix approach as Step 2. The TS POM `entry-form-page.ts` uses `getByLabel('URL')` inside a `linkRow(index)` locator scoped to `[data-link-row]`. Since the link rows are inside the collapse, if filling fails, add a step in the POM's `fillLinkRow` method to expand the collapse first. But try running tests first — this may not be needed.

- [ ] **Step 5: Commit any test fixes**

```bash
git add tests/ tstests/
git commit -m "test: fix E2E selectors for entry form section restructure"
```

(Skip this commit if no test changes were needed.)

---

## Task 5: Final verification and cleanup

- [ ] **Step 1: Run the full Python test suite (unit + integration + E2E)**

Run: `uv run pytest tests/ -v --timeout=60 2>&1 | tail -20`

Expect all tests to pass.

- [ ] **Step 2: Run the full TypeScript E2E suite**

Run: `npm run test:e2e:ts 2>&1 | tail -20`

Expect all tests to pass.

- [ ] **Step 3: Manual smoke test of edit mode**

Open an existing entry's edit page. Verify:
- All fields pre-populated correctly
- Summary Instructions toggle is expanded (if the entry had instructions — note: instructions aren't stored, so this will always be collapsed in edit mode, which is correct)
- Additional Links toggle is expanded if the entry has links
- Generate button works and auto-fills fields with highlight animation
- Save works and redirects to view page

- [ ] **Step 4: Manual smoke test of validation errors**

Submit the new entry form with empty required fields. Verify:
- Error messages appear on the correct fields
- Form state is preserved
- Collapsed sections with errors auto-expand (if applicable — currently no required fields are in collapsed sections, so this is N/A)
