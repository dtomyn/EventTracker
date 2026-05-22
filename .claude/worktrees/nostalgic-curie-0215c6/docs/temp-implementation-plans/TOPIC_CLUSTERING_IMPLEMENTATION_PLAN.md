# Implementation Plan: Topic Clustering and Mind Map (FR-010)

This plan breaks down the implementation of the Topic Clustering and Mind Map feature into four distinct modules. These modules are isolated enough that they can be assigned to different subagents or executed sequentially.

## Overview
- **Goal:** Implement semantic topic clustering using `sqlite-vec` embeddings and a dynamic D3.js mind map for the currently selected timeline group.
- **Reference:** `docs/functional-requirements/FR-010-topic-clustering.md`

## Current Status
- Phase 1 backend service has been implemented in `app/services/topics.py` and type-checked successfully after fixing integration issues discovered during implementation.
- Phase 2 API routes and page template scaffolds have been created in `app/main.py`.
- Remaining work is now primarily D3 template rendering and UI navigation integration.

## Lessons Learned From Phase 2
- **Direct serialization of Dataclasses:** FastAPI handles Pydantic models automatically, but pure Python dataclasses require `dataclasses.asdict()` before returning for clean JSON serialization.
- **Template context consistency:** Providing identical context keys (like `selected_group_id` and `timeline_filters`) to the `topic_graph.html` template maintains a consistent shell layout with the timeline page.

## Lessons Learned From Phase 1
- **Prefer direct file edits over shell-generated multiline writes:** the topic service contains nested Python, SQL, and prompt strings, which made shell-based file generation brittle. For follow-up work, edit files directly rather than generating source through terminal string interpolation.
- **Copilot runtime calls require explicit timeout values:** `copilot_runtime.send_copilot_prompt(...)` must be called with a named `timeout` argument. Future Copilot-backed routes or background operations should follow the same pattern.
- **Graceful degradation is already part of the backend contract:** `build_topic_graph(...)` returns an empty graph when `sqlite-vec` is unavailable or embeddings are missing. Phase 2 should preserve that behavior at the API layer rather than converting it into a hard failure.
- **Cluster labeling is network/provider dependent:** the graph structure can be built locally, but AI-generated labels may fail independently. Phase 2 and Phase 3 should tolerate unlabeled or fallback-labeled nodes without blocking rendering.
- **The payload shape is already settled in code:** the service currently returns `nodes` with `id`, `label`, `entry_ids`, `size` and `edges` with `source`, `target`, `weight`. The API route should expose this shape directly to avoid unnecessary transformation layers.
- **Validation should happen per phase, not only at the end:** a fast `pyright` pass caught a real integration issue immediately. Continue validating each phase as it lands.

---

## Phase 1: Offline Backend Clustering Script (Subagent 1)
**Focus:** Data extraction, semantic clustering, JSON geometry generation, and persistence.

**Status:** Completed (In-memory), needing update for offline execution.

### Tasks:
1. **Schema Update:**
   - Create a `topic_cluster_cache` table mapping `group_id` to a `graph_json` blob and an `updated_utc` timestamp.
2. **Offline Script:** 
   - Create a CLI script (e.g., `scripts/compute_topic_clusters.py`) that precomputes the graph data and saves the JSON to the new `topic_cluster_cache` table for all or specified groups. This prevents timeouts from happening in the request-response lifecycle.
3. **Data Retrieval:**
   - Fetch embeddings for all entries matching the requested `group_id`.
   - Handle the case where `sqlite-vec` is not populated or available (graceful degradation).
4. **Clustering Algorithm:**
   - Implement a lightweight grouping mechanism (e.g., threshold-based cosine similarity grouping using raw Python, or using a lightweight k-means).
5. **Topic Label Generation:**
   - Use the existing AI provider abstraction (`app/services/ai_generate.py`) to summarize the entries in a cluster into a short 1-3 word label.
6. **Graph Geometry Persistence:**
   - Construct the nodes and edges JSON payload:
     - `nodes`: `[{ id, label, size (entry count), entry_ids }]`
     - `edges`: `[{ source, target, weight (similarity/proximity) }]`
   - Store the serialized payload into the `topic_cluster_cache` table.

---

## Phase 2: API Endpoints & Routes (Subagent 2)
**Focus:** Exposing the precomputed clustering data and view via FastAPI.

**Status:** Completed (Dynamic), needing update to read from cache.

### Tasks:
1. **API Route (JSON):**
   - Refactor `GET /api/groups/{group_id}/topics` in `app/main.py`.
   - Ensure the endpoint just fetches the precomputed graph JSON from `topic_cluster_cache` using the group ID.
   - Return an empty graph structure if no cache exists, or if embeddings are disabled.
2. **Page Route (HTML):**
   - Add a route `GET /topics` or `GET /groups/{group_id}/topics/graph` in `app/main.py`.
   - Ensure it maps to a new template (`app/templates/topic_graph.html`).
   - Validate the `group_id` context similar to how the timeline limits its scope.
   - Avoid recomputing clustering logic in multiple places; reuse the same service path for both HTML and JSON flows where practical.

---

## Phase 3: Frontend D3.js Visualization (Subagent 3)
**Focus:** Building the client-side D3 graph.

**Status:** Completed

### Tasks:
1. **Template Scaffold:**
   - Create `app/templates/topic_graph.html` extending `base.html`.
2. **D3.js Setup:**
   - Include the `d3.js` library via CDN in the template block.
3. **Graph Rendering:**
   - Fetch the graph JSON from the Phase 2 API endpoint.
   - Render a force-directed graph (nodes and links).
   - Scale node radiuses based on the `size` property.
   - Adjust link distances/thickness based on the `weight` property.
   - Treat missing or fallback labels as valid input so the graph remains usable even when provider labeling fails.
4. **Interactivity:**
   - Add click event listeners to nodes.
   - Clicking a node should redirect the browser to `/?q={TopicLabel}&group_id={group_id}` or use a specific tag/entry ID filter depending on the easiest match mechanism.
   - Add dedicated `Zoom in` and `Zoom out` buttons that drive the D3 zoom behavior programmatically, so the graph remains usable on devices where gesture zoom is awkward or unavailable.

---

## Phase 4: UI Integration & Styling (Subagent 4)
**Focus:** Navigation, dark mode, and polish.

### Tasks:
1. **Navigation Entry Points:**
   - Add a "Mind Map" or "Topic Graph" button in the group selection area or timeline view header for the currently selected group.
   - Ensure the button is hidden or disabled if embeddings are turned off globally.
2. **Dark Mode Support:**
   - Hook into the existing Bootstrap `[data-bs-theme="dark"]` ecosystem.
   - Update the D3.js rendering logic to read CSS custom properties (e.g., `var(--bs-primary)`) for node fills and text colors so it toggles seamlessly when the user changes themes.
3. **Responsive Details:**
   - Make the SVG canvas responsive to window resizing.
   - Add basic loading states (spinners) while the backend generative clustering is running.
   - Position the zoom controls so they remain visible and usable across desktop and mobile layouts.

---
## Review and Testing
- Run Pyright checking (`uv run pyright`) after each phase.
- Validate standard functionality with existing pytest suites.
- Add focused tests for edge cases: a group with 0 entries, a group with 1 entry, embeddings disabled, and AI label generation fallback behavior.
- Verify the zoom buttons change the visible graph scale in the rendered page without breaking drag, click, or resize behavior.