# FR-014 Connection Graph Visualization

- Category: Functional
- Status: Baseline
- Scope: Visual graph display of entry-to-entry connections and tag co-occurrence relationships within a timeline group using D3.js.
- Primary Sources: `app/main.py`, `app/services/entries.py`, `app/templates/connection_graph.html`, `tests/test_entries.py`

## Requirement Statements

- FR-014-01 The system shall provide a dedicated route (`/groups/{group_id}/connections/graph`) to view the entry connection graph for a timeline group.
- FR-014-02 The system shall expose an API endpoint (`/api/groups/{group_id}/connections`) that returns connection graph data as JSON for client-side rendering.
- FR-014-03 The system shall accept an optional `include_tags` query parameter to include tag co-occurrence edges alongside entry-connection edges.
- FR-014-04 The system shall build the connection graph by traversing accepted entry connections and generating nodes for entries and edges for relationships.
- FR-014-05 The system shall scale graph node sizes relative to the number of connections for that entry.
- FR-014-06 The system shall render edges between connected entries, using edge thickness or weight to indicate connection strength or co-occurrence frequency.
- FR-014-07 The system shall render the graph on the client side using the `d3.js` library fed by the backend's JSON payload.
- FR-014-08 The system shall support dark mode by respecting the host page's `[data-bs-theme="dark"]` CSS custom properties for node, edge, and background colors.
- FR-014-09 The system shall allow users to click on an entry node in the connection graph to navigate to that entry's detail page.
- FR-014-10 The system shall provide zoom in and zoom out controls to adjust the displayed visualization.
- FR-014-11 The system shall gracefully return an empty graph when no connections or entries exist for a group.
- FR-014-12 The system shall scope graph data generation strictly to the entries and connections belonging to the currently selected timeline group.

## Acceptance Notes

- Connection graphs are accessible from the main navigation or group sidebar when a valid group is selected.
- The graph correctly switches styling when toggling the system or app-level dark mode preference.
- The graph exposes visible zoom controls that work consistently on desktop and touch-constrained devices.
- The visualization handles large entry groups without visual crowding or breaking the layout.
- Entry connections created through the suggested-connections acceptance workflow appear in the graph.
