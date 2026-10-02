# FR-015 Poster Board

- Category: Functional
- Status: Baseline
- Scope: One-screen corkboard view of the most important entries in the current timeline scope, with category filtering, client-side search, a detail sheet per entry, and a self-contained HTML export.
- Primary Sources: `app/main.py`, `app/services/poster_board.py`, `app/templates/poster_board.html`, `app/templates/poster_board/board.css`, `app/templates/poster_board/board.js`, `tests/test_poster_board.py`, `tstests/e2e/poster-board.spec.ts`

## Requirement Statements

- FR-015-01 The system shall expose `GET /timeline/board` to render a poster board for the same scope as the timeline: the selected group (or `All groups`) and the optional `q` filter.
- FR-015-02 The system shall accept an optional `year` parameter that limits the board to entries from that year, and shall reject a non-numeric or out-of-range year with HTTP 422.
- FR-015-03 The system shall accept an optional `limit` parameter and normalize it to one of 20, 30, 45, or 60 posters, defaulting to 30.
- FR-015-04 The system shall rank scoped entries by a heuristic importance score built from connection count, additional link count, presence of a source URL, text length, tag count, and recency within the scope.
- FR-015-05 The system shall keep the top-ranked entries up to the limit and assign them to four size tiers in roughly a 10% / 30% / 50% / 10% mix, with poster size as the only visual signal of importance.
- FR-015-06 The system shall color posters by timeline group when the board spans more than one group, and otherwise by each entry's most common tag, folding categories beyond the eighth into `Other` and tagless entries into `Untagged`.
- FR-015-07 The board shall provide category chips and a search box that filter posters on the client without a page reload.
- FR-015-08 The board shall open a detail sheet for a poster showing the sanitized entry text, date, group, connected events, sources, tags, and previous/next navigation.
- FR-015-09 The detail sheet shall let users jump to a connected event that is also on the board.
- FR-015-10 The in-app board shall link each detail sheet to the full entry page, and the timeline shall link to the board for the current scope.
- FR-015-11 The system shall expose `GET /timeline/board/export` with the same parameters, returning the board as an HTML attachment named `EventTracker-board-<scope>-<date>.html`.
- FR-015-12 The exported file shall be self-contained: styles, script, and data are inlined, nothing is loaded from the network, and it contains no links back into the application.

## Acceptance Notes

- Entry text shown on the board is sanitized server-side with the same allow-list as entry detail pages before it is embedded.
- The board data is embedded as JSON with HTML-significant characters escaped, so entry content cannot break out of its script block.
- On narrow or short viewports the board falls back to a scrolling single-column list.
