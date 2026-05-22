# EventTracker Pages And API Routes

## Core Locations

- FastAPI app and all route decorators are defined in `app/main.py`.
- Jinja templates are configured in `app/main.py:127` and stored under `app/templates/`.
- Static assets are mounted from `app/static/` in `app/main.py:148`.
- There are no separate `APIRouter` modules in the current app; the route surface is centralized in `app/main.py`.

## Full Pages

| Route | Method | Purpose | Defined in | Template / behavior |
| --- | --- | --- | --- | --- |
| `/` | GET | Main timeline page | `app/main.py:557` (`timeline`) | `app/templates/timeline.html` |
| `/search` | GET | Search page with first result page | `app/main.py:1040` (`ranked_search`) | `app/templates/search.html` |
| `/story` | GET | Story generation page for the current scope | `app/main.py:1134` (`story_page`) | `app/templates/story.html` |
| `/story/{story_id:int}` | GET | Saved story detail page | `app/main.py:1400` (`saved_story_page`) | `app/templates/story.html` |
| `/groups/{group_id}/topics/graph` | GET | Topic cluster graph page for a group | `app/main.py:1507` (`group_topics_graph`) | `app/templates/topic_graph.html` |
| `/entries/new` | GET | New entry form | `app/main.py:1569` (`new_entry_form`) | `app/templates/entry_form.html` |
| `/entries/{entry_id:int}/view` | GET | Read-only entry detail page | `app/main.py:1591` (`view_entry`) | `app/templates/entry_detail.html` |
| `/entries/{entry_id:int}` | GET | Edit entry form | `app/main.py:1661` (`edit_entry_form`) | `app/templates/entry_form.html` |
| `/admin/groups` | GET | Timeline group administration page | `app/main.py:1737` (`manage_groups`) | `app/templates/admin_groups.html` |
| `/visualization` | GET | Legacy visualization entry point | `app/main.py:1492` (`timeline_visualization`) | Redirects to `/` |

## Page Actions And HTML Fragment Endpoints

These routes support the UI but are not standalone pages.

| Route | Method | Purpose | Defined in | Response |
| --- | --- | --- | --- | --- |
| `/story/generate` | POST | Generates a story for the selected scope | `app/main.py:1178` (`generate_story_page`) | Renders `app/templates/story.html` |
| `/story/save` | POST | Saves a generated story | `app/main.py:1310` (`save_story_page`) | Redirect to `/story/{story_id}` |
| `/entries/new` | POST | Creates a new entry | `app/main.py:1609` (`create_entry`) | Redirect to `/entries/{entry_id}/view` or re-render form |
| `/entries/{entry_id:int}` | POST | Updates an existing entry | `app/main.py:1682` (`update_entry_route`) | Redirect to `/entries/{entry_id}/view` or re-render form |
| `/admin/groups` | POST | Creates a timeline group | `app/main.py:1763` (`create_group_route`) | Redirect to `/admin/groups` or re-render page |
| `/admin/groups/{group_id:int}` | POST | Renames or updates a timeline group | `app/main.py:1800` (`rename_group_route`) | Redirect to `/admin/groups` or re-render page |
| `/admin/groups/{group_id:int}/delete` | POST | Deletes a timeline group | `app/main.py:1853` (`delete_group_route`) | Redirect to `/admin/groups` or re-render page |
| `/entries/generate` | POST | AI-assisted entry draft generation | `app/main.py:1879` (`generate_entry_preview`) | Renders `app/templates/partials/generated_preview.html` |
| `/entries/preview-html` | POST | Sanitized live HTML preview for entry content | `app/main.py:2006` (`preview_entry_html`) | Renders `app/templates/partials/html_preview_content.html` |

## API-Style Routes

These routes primarily return JSON, SSE, or downloadable data.

| Route | Method | Purpose | Defined in | Response |
| --- | --- | --- | --- | --- |
| `/timeline/group-web-search` | GET | Returns cached or live group-related web search results | `app/main.py:605` (`timeline_group_web_search`) | JSON |
| `/timeline/group-web-search/stream` | GET | Streams group web search progress/results | `app/main.py:698` (`timeline_group_web_search_stream`) | Server-Sent Events |
| `/timeline/group-web-search/refresh` | POST | Forces a fresh group web search | `app/main.py:829` (`refresh_timeline_group_web_search`) | JSON |
| `/timeline/details` | GET | Returns paged timeline detail HTML inside a JSON envelope | `app/main.py:923` (`timeline_details`) | JSON using `app/templates/partials/timeline_detail_groups.html` |
| `/timeline/years` | GET | Returns year buckets for the current scope | `app/main.py:954` (`timeline_years`) | JSON using `app/templates/partials/timeline_bucket_cards.html` |
| `/timeline/months` | GET | Returns month buckets, optionally filtered by year | `app/main.py:980` (`timeline_months`) | JSON using `app/templates/partials/timeline_bucket_cards.html` |
| `/timeline/summaries` | GET | Returns grouped event summaries for the current scope | `app/main.py:1010` (`timeline_summaries`) | JSON using `app/templates/partials/timeline_summary_groups.html` |
| `/search/results` | GET | Returns paged search results for incremental loading | `app/main.py:1094` (`ranked_search_results`) | JSON using `app/templates/partials/search_results.html` |
| `/api/groups/{group_id}/topics` | GET | Returns cached topic graph data for a group | `app/main.py:1497` (`api_group_topics`) | JSON |
| `/entries/export` | GET | Exports all entries as a downloadable JSON file | `app/main.py:1529` (`export_entries`) | JSON download attachment |
| `/dev/extract` | GET | Developer-only content extraction probe for a URL | `app/main.py:2022` (`dev_extract`) | JSON |

## Template Inventory

Main templates in `app/templates/`:

- `timeline.html`
- `search.html`
- `story.html`
- `topic_graph.html`
- `entry_form.html`
- `entry_detail.html`
- `admin_groups.html`
- `base.html`

Partial templates in `app/templates/partials/`:

- `timeline_detail_groups.html`
- `timeline_bucket_cards.html`
- `timeline_summary_groups.html`
- `search_results.html`
- `generated_preview.html`
- `html_preview_content.html`
- `entry_card.html`
- `story_links.html`