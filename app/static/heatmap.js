// Heatmap view for the timeline page: calendar grid, tooltip, date filter, and time-lapse playback.
// All user-provided text is written with textContent / DOM APIs, never innerHTML.
(() => {
    "use strict";

    const SVG_NS = "http://www.w3.org/2000/svg";
    const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""];
    const LABEL_WIDTH = 34;
    const TOP_MARGIN = 22;
    const MIN_STEP = 13;
    const MAX_STEP = 24;
    const PLAYBACK_DURATION_MS = 10000;
    const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

    const state = {
        data: null,
        groupId: "",
        requestId: 0,
        cells: [],
        weekCount: 0,
        weekTotals: [],
        revealed: 0,
        timer: null,
        selectedDate: null,
        focusedIndex: 0,
        lastWidth: 0,
        els: null,
    };

    const getContainer = () => document.getElementById("heatmap-container");
    const getEntriesContainer = () => document.getElementById("heatmap-entries");
    const getViewButton = () => document.querySelector('[data-zoom-target="heatmap"]');

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function svgEl(tag, attrs) {
        const node = document.createElementNS(SVG_NS, tag);
        for (const [key, value] of Object.entries(attrs || {})) {
            node.setAttribute(key, String(value));
        }
        return node;
    }

    function pad(value) {
        return String(value).padStart(2, "0");
    }

    function formatLong(date) {
        return date.toLocaleDateString("en-US", { weekday: "short", month: "long", day: "numeric", year: "numeric" });
    }

    function formatShort(date) {
        return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    }

    function pluralEvents(count) {
        return `${count.toLocaleString("en-US")} ${count === 1 ? "event" : "events"}`;
    }

    function setBusy(isBusy) {
        const container = getContainer();
        if (container) container.setAttribute("aria-busy", String(isBusy));
        const button = getViewButton();
        if (button) {
            if (isBusy) button.setAttribute("aria-busy", "true");
            else button.removeAttribute("aria-busy");
        }
    }

    // --- Loading skeleton ---

    function renderSkeleton(container) {
        container.replaceChildren();
        const skeleton = el("div", "hm-skeleton");
        skeleton.setAttribute("aria-hidden", "true");
        const header = el("div", "hm-skeleton-header");
        header.append(el("span", "hm-skeleton-bar hm-skeleton-bar--year"), el("span", "hm-skeleton-bar hm-skeleton-bar--total"));
        const grid = el("div", "hm-skeleton-grid");
        for (let i = 0; i < 53 * 7; i += 1) {
            grid.append(el("span", "hm-skeleton-cell"));
        }
        const footer = el("div", "hm-skeleton-header");
        footer.append(el("span", "hm-skeleton-bar hm-skeleton-bar--play"), el("span", "hm-skeleton-bar hm-skeleton-bar--legend"));
        skeleton.append(header, grid, footer);
        const status = el("span", "visually-hidden", "Loading heatmap");
        status.setAttribute("role", "status");
        container.append(skeleton, status);
    }

    // --- Data shaping ---

    function buildCells(data) {
        const { counts, titles, year } = data;
        const jan1 = new Date(year, 0, 1);
        const startDay = (jan1.getDay() + 6) % 7; // Monday = 0
        const cells = [];
        for (let date = new Date(year, 0, 1); date.getFullYear() === year; date.setDate(date.getDate() + 1)) {
            const dayOfYear = Math.round((date - jan1) / 86400000);
            const key = `${year}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
            cells.push({
                key,
                date: new Date(date),
                week: Math.floor((dayOfYear + startDay) / 7),
                dayOfWeek: (date.getDay() + 6) % 7,
                month: date.getMonth(),
                dayNum: date.getDate(),
                count: counts[key] || 0,
                titles: (titles && titles[key]) || [],
                node: null,
            });
        }
        return cells;
    }

    function levelFor(count, maxCount) {
        if (count <= 0) return 0;
        const ratio = Math.sqrt(count / maxCount);
        if (ratio <= 0.25) return 1;
        if (ratio <= 0.5) return 2;
        if (ratio <= 0.75) return 3;
        return 4;
    }

    function cellLabel(cell) {
        const day = formatLong(cell.date);
        return cell.count === 0
            ? `No entries on ${day}`
            : `${cell.count} ${cell.count === 1 ? "entry" : "entries"} on ${day}`;
    }

    // --- Rendering ---

    function load({ groupId = "", year = null } = {}) {
        const container = getContainer();
        if (!container) return;
        stopPlayback();
        state.groupId = String(groupId || "");
        const requestId = ++state.requestId;
        const params = new URLSearchParams();
        if (state.groupId) params.set("group_id", state.groupId);
        if (year) params.set("year", String(year));

        renderSkeleton(container);
        setBusy(true);

        fetch(`/api/heatmap?${params}`)
            .then((response) => {
                if (!response.ok) throw new Error(`Request failed (${response.status})`);
                return response.json();
            })
            .then((data) => {
                if (requestId !== state.requestId) return;
                if (state.selectedDate && !state.selectedDate.startsWith(`${data.year}-`)) {
                    clearFilter();
                }
                state.data = data;
                render();
            })
            .catch((error) => {
                if (requestId !== state.requestId) return;
                container.replaceChildren(el("div", "hm-error text-danger", `Could not load heatmap: ${error.message}`));
            })
            .finally(() => {
                if (requestId === state.requestId) setBusy(false);
            });
    }

    function render() {
        const container = getContainer();
        const data = state.data;
        if (!container || !data) return;
        stopPlayback();

        const { total, year, years_available: years } = data;
        const cells = buildCells(data);
        const weekCount = cells[cells.length - 1].week + 1;
        const maxCount = Math.max(1, ...cells.map((cell) => cell.count));
        state.cells = cells;
        state.weekCount = weekCount;
        state.weekTotals = new Array(weekCount).fill(0);
        for (const cell of cells) state.weekTotals[cell.week] += cell.count;
        state.revealed = weekCount;

        // Size cells from the available width so the grid fills the card.
        const available = Math.max(0, container.clientWidth - LABEL_WIDTH);
        const step = Math.max(MIN_STEP, Math.min(MAX_STEP, Math.floor(available / weekCount)));
        const gap = Math.max(2, Math.round(step * 0.16));
        const size = step - gap;
        const gridWidth = weekCount * step - gap;
        const svgWidth = LABEL_WIDTH + gridWidth;
        const svgHeight = TOP_MARGIN + 7 * step - gap + 2;
        state.lastWidth = container.clientWidth;

        const root = el("div", "hm-root");
        root.style.setProperty("--hm-grid-width", `${svgWidth}px`);
        root.style.setProperty("--hm-label-width", `${LABEL_WIDTH}px`);

        // Header: year navigation + total
        const header = el("div", "hm-header");
        const nav = el("div", "hm-nav");
        const index = years.indexOf(year);
        const prevYear = index > 0 ? years[index - 1] : undefined;
        const nextYear = index >= 0 && index < years.length - 1 ? years[index + 1] : undefined;
        const makeNav = (glyph, label, target) => {
            const button = el("button", "heatmap-nav-arrow", glyph);
            button.type = "button";
            button.setAttribute("aria-label", target === undefined ? label : `${label} (${target})`);
            if (target === undefined) {
                button.disabled = true;
            } else {
                button.addEventListener("click", () => load({ groupId: state.groupId, year: target }));
            }
            return button;
        };
        const yearLabel = el("span", "hm-year", String(year));
        yearLabel.setAttribute("aria-live", "polite");
        nav.append(makeNav("◀", "Previous year", prevYear), yearLabel, makeNav("▶", "Next year", nextYear));
        const totalLabel = el("span", "hm-total", `${pluralEvents(total)} in ${year}`);
        header.append(nav, totalLabel);

        // Grid
        const scroll = el("div", "hm-scroll");
        // Weekday labels live in their own sticky column so they stay visible
        // while the grid scrolls horizontally on narrow screens.
        const daySvg = svgEl("svg", {
            class: "hm-day-labels",
            width: LABEL_WIDTH,
            height: svgHeight,
            viewBox: `0 0 ${LABEL_WIDTH} ${svgHeight}`,
            "aria-hidden": "true",
        });
        const dayG = svgEl("g", { transform: `translate(${LABEL_WIDTH}, ${TOP_MARGIN})` });
        daySvg.append(dayG);
        const svg = svgEl("svg", {
            class: "heatmap-svg",
            width: gridWidth,
            height: svgHeight,
            viewBox: `0 0 ${gridWidth} ${svgHeight}`,
            role: "group",
            "aria-label": `Entry activity heatmap for ${year}. Use arrow keys to move between days.`,
        });
        const gridG = svgEl("g", { transform: `translate(0, ${TOP_MARGIN})` });
        svg.append(gridG);

        // Month labels sit above the first full week column of each month.
        let lastLabelWeek = -10;
        for (const cell of cells) {
            if (cell.dayNum !== 1) continue;
            const week = cell.dayOfWeek === 0 || cell.week === 0 ? cell.week : cell.week + 1;
            if (week - lastLabelWeek < 3 || week >= weekCount) continue;
            const text = svgEl("text", { class: "hm-axis-label", x: week * step, y: -8 });
            text.textContent = MONTHS[cell.month];
            gridG.append(text);
            lastLabelWeek = week;
        }

        DAY_LABELS.forEach((label, i) => {
            if (!label) return;
            const text = svgEl("text", {
                class: "hm-axis-label",
                x: -8,
                y: i * step + size / 2,
                "text-anchor": "end",
                "dominant-baseline": "central",
            });
            text.textContent = label;
            dayG.append(text);
        });

        const radius = Math.max(2, Math.round(size * 0.24));
        let firstActive = -1;
        cells.forEach((cell, i) => {
            const level = levelFor(cell.count, maxCount);
            const rect = svgEl("rect", {
                class: `heatmap-cell hm-l${level}`,
                x: cell.week * step,
                y: cell.dayOfWeek * step,
                width: size,
                height: size,
                rx: radius,
                "data-date": cell.key,
                "data-count": cell.count,
                "data-week": cell.week,
                role: "button",
                tabindex: -1,
                "aria-label": cellLabel(cell),
            });
            if (cell.count > 0) rect.classList.add("has-entries");
            if (cell.key === state.selectedDate) rect.classList.add("heatmap-cell-selected");
            rect.addEventListener("mouseenter", () => showTooltip(cell));
            rect.addEventListener("mouseleave", hideTooltip);
            rect.addEventListener("focus", () => {
                state.focusedIndex = i;
                showTooltip(cell);
            });
            rect.addEventListener("blur", hideTooltip);
            rect.addEventListener("click", () => activateCell(cell));
            rect.addEventListener("keydown", (event) => onCellKeydown(event, i));
            rect.addEventListener("animationend", () => rect.classList.remove("is-pop"));
            cell.node = rect;
            gridG.append(rect);
            if (firstActive < 0 && cell.count > 0) firstActive = i;
        });
        state.focusedIndex = Math.max(0, firstActive);
        cells[state.focusedIndex].node.setAttribute("tabindex", "0");

        if (total === 0) {
            const empty = svgEl("text", {
                class: "hm-empty-label",
                x: gridWidth / 2,
                y: TOP_MARGIN + 3.5 * step,
                "text-anchor": "middle",
                "dominant-baseline": "central",
            });
            empty.textContent = `No events tracked in ${year}`;
            svg.append(empty);
        }
        scroll.append(daySvg, svg);

        // Footer: playback controls + legend
        const footer = el("div", "hm-footer");
        const playback = el("div", "hm-playback");
        const playButton = el("button", "hm-play");
        playButton.type = "button";
        playButton.append(el("span", "hm-play-icon"));
        playButton.setAttribute("aria-label", "Play time-lapse");
        playButton.setAttribute("aria-pressed", "false");
        if (reducedMotionQuery.matches || total === 0) {
            playButton.disabled = true;
            playButton.title = total === 0 ? "Nothing to replay" : "Animation is off because reduced motion is enabled. Use the slider instead.";
        }
        playButton.addEventListener("click", togglePlayback);

        const scrubber = el("input", "hm-scrubber form-range");
        scrubber.type = "range";
        scrubber.min = "0";
        scrubber.max = String(weekCount);
        scrubber.step = "1";
        scrubber.value = String(weekCount);
        scrubber.setAttribute("aria-label", `Time-lapse position, week of ${year}`);
        scrubber.addEventListener("input", () => {
            stopPlayback();
            reveal(Number(scrubber.value), false);
        });

        const counter = el("output", "hm-counter");
        counter.setAttribute("aria-live", "polite");
        playback.append(playButton, scrubber, counter);

        const legend = el("div", "hm-legend");
        legend.setAttribute("aria-hidden", "true");
        legend.append(el("span", "hm-legend-label", "Less"));
        for (let level = 0; level <= 4; level += 1) {
            legend.append(el("span", `hm-legend-swatch hm-l${level}`));
        }
        legend.append(el("span", "hm-legend-label", "More"));
        footer.append(playback, legend);

        const tooltip = el("div", "heatmap-tooltip");
        tooltip.setAttribute("role", "tooltip");
        tooltip.hidden = true;

        root.append(header, scroll, footer);
        container.replaceChildren(root, tooltip);
        state.els = { container, root, svg, playButton, scrubber, counter, tooltip };
        reveal(weekCount, false);

        // On narrow screens the grid scrolls; bring the most recent activity into view.
        const lastActive = [...cells].reverse().find((cell) => cell.count > 0);
        if (lastActive && scroll.scrollWidth > scroll.clientWidth) {
            const x = LABEL_WIDTH + lastActive.week * step;
            scroll.scrollLeft = Math.max(0, x - scroll.clientWidth * 0.7);
        }
    }

    // --- Tooltip ---

    function showTooltip(cell) {
        const els = state.els;
        if (!els || !cell.node) return;
        const { tooltip, container } = els;
        const isFuture = cell.week >= state.revealed;
        const date = el("div", "hm-tip-date", formatLong(cell.date));
        const count = el("div", "hm-tip-count");
        if (isFuture) {
            count.textContent = "Not revealed yet";
        } else if (cell.count === 0) {
            count.textContent = "No entries";
        } else {
            count.textContent = `${cell.count} ${cell.count === 1 ? "entry" : "entries"}`;
        }
        const children = [date, count];
        if (!isFuture && cell.titles.length) {
            const list = el("ul", "hm-tip-titles");
            for (const title of cell.titles) list.append(el("li", "", title));
            if (cell.count > cell.titles.length) {
                list.append(el("li", "hm-tip-more", `+${cell.count - cell.titles.length} more`));
            }
            children.push(list);
        }
        tooltip.replaceChildren(...children);
        tooltip.hidden = false;

        const cellRect = cell.node.getBoundingClientRect();
        const hostRect = container.getBoundingClientRect();
        const tipRect = tooltip.getBoundingClientRect();
        const centerX = cellRect.left - hostRect.left + cellRect.width / 2;
        const half = tipRect.width / 2;
        const left = Math.min(Math.max(centerX, half + 4), hostRect.width - half - 4);
        let top = cellRect.top - hostRect.top - tipRect.height - 10;
        const below = top < -hostRect.top + 8;
        if (below) top = cellRect.bottom - hostRect.top + 10;
        tooltip.classList.toggle("is-below", below);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${top}px`;
        tooltip.style.setProperty("--hm-arrow-offset", `${centerX - left}px`);
    }

    function hideTooltip() {
        if (state.els) state.els.tooltip.hidden = true;
    }

    // --- Keyboard navigation (roving tabindex) ---

    function onCellKeydown(event, index) {
        const deltas = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 };
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            activateCell(state.cells[index]);
            return;
        }
        let next = null;
        if (event.key in deltas) next = index + deltas[event.key];
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = state.cells.length - 1;
        if (next === null) return;
        event.preventDefault();
        next = Math.min(Math.max(next, 0), state.cells.length - 1);
        state.cells[index].node.setAttribute("tabindex", "-1");
        const target = state.cells[next].node;
        target.setAttribute("tabindex", "0");
        target.focus();
    }

    // --- Date filter ---

    function activateCell(cell) {
        if (cell.count === 0 || cell.week >= state.revealed) return;
        if (state.selectedDate === cell.key) {
            clearFilter();
            return;
        }
        selectDate(cell.key);
    }

    function markSelected() {
        for (const cell of state.cells) {
            if (cell.node) cell.node.classList.toggle("heatmap-cell-selected", cell.key === state.selectedDate);
        }
    }

    function selectDate(dateKey) {
        state.selectedDate = dateKey;
        markSelected();
        const [year, month, day] = dateKey.split("-");
        const params = new URLSearchParams({ year, month: String(Number(month)), day: String(Number(day)) });
        if (state.groupId) params.set("group_id", state.groupId);

        const entriesContainer = getEntriesContainer();
        if (!entriesContainer) return;
        entriesContainer.replaceChildren(el("div", "text-body-secondary py-3", "Loading entries…"));

        fetch(`/timeline/heatmap/entries?${params}`)
            .then((response) => {
                if (!response.ok) throw new Error("Failed");
                return response.text();
            })
            .then((html) => {
                if (state.selectedDate !== dateKey) return;
                // Server-rendered, auto-escaped Jinja partial.
                const fragment = document.createRange().createContextualFragment(html);
                entriesContainer.replaceChildren(fragment);
                const clearButton = entriesContainer.querySelector("[data-heatmap-clear-filter]");
                if (clearButton) clearButton.addEventListener("click", clearFilter);
            })
            .catch(() => {
                entriesContainer.replaceChildren(el("div", "text-danger py-3", "Could not load entries."));
            });
    }

    function clearFilter() {
        state.selectedDate = null;
        markSelected();
        const entriesContainer = getEntriesContainer();
        if (entriesContainer) entriesContainer.replaceChildren();
    }

    // --- Time-lapse ---

    function counterText(revealed) {
        const year = state.data.year;
        if (revealed <= 0) return `0 events before Jan 1, ${year}`;
        let sum = 0;
        for (let week = 0; week < revealed; week += 1) sum += state.weekTotals[week];
        const lastCell = [...state.cells].reverse().find((cell) => cell.week === revealed - 1);
        return `${pluralEvents(sum)} through ${formatShort(lastCell.date)}, ${year}`;
    }

    function reveal(revealed, animate) {
        const els = state.els;
        if (!els) return;
        const previous = state.revealed;
        state.revealed = revealed;
        const pop = animate && !reducedMotionQuery.matches;
        for (const cell of state.cells) {
            const isFuture = cell.week >= revealed;
            const wasFuture = cell.week >= previous;
            cell.node.classList.toggle("is-future", isFuture);
            if (pop && wasFuture && !isFuture && cell.count > 0) {
                cell.node.classList.remove("is-pop");
                // Restart the animation on the freshly revealed cell.
                void cell.node.getBoundingClientRect();
                cell.node.classList.add("is-pop");
            }
        }
        els.root.classList.toggle("is-partial", revealed < state.weekCount);
        els.scrubber.value = String(revealed);
        const text = counterText(revealed);
        els.counter.textContent = text;
        els.scrubber.setAttribute("aria-valuetext", text);
    }

    function setPlayingUi(isPlaying) {
        const els = state.els;
        if (!els) return;
        els.playButton.classList.toggle("is-playing", isPlaying);
        els.playButton.setAttribute("aria-pressed", String(isPlaying));
        els.playButton.setAttribute("aria-label", isPlaying ? "Pause time-lapse" : "Play time-lapse");
        els.root.classList.toggle("is-playing", isPlaying);
    }

    function stopPlayback() {
        if (state.timer !== null) {
            window.clearInterval(state.timer);
            state.timer = null;
        }
        setPlayingUi(false);
    }

    function togglePlayback() {
        if (state.timer !== null) {
            stopPlayback();
            return;
        }
        if (reducedMotionQuery.matches || !state.els) return;
        hideTooltip();
        if (state.revealed >= state.weekCount) reveal(0, false);
        setPlayingUi(true);
        const interval = PLAYBACK_DURATION_MS / state.weekCount;
        state.timer = window.setInterval(() => {
            const next = state.revealed + 1;
            reveal(next, true);
            if (next >= state.weekCount) stopPlayback();
        }, interval);
    }

    // --- Responsiveness ---

    let resizeFrame = 0;
    const resizeObserver = new ResizeObserver(() => {
        const container = getContainer();
        if (!container || !state.data || !state.els || container.clientWidth === 0) return;
        if (Math.abs(container.clientWidth - state.lastWidth) < 8) return;
        window.cancelAnimationFrame(resizeFrame);
        resizeFrame = window.requestAnimationFrame(() => {
            const revealed = state.revealed;
            render();
            reveal(revealed, false);
        });
    });

    function init() {
        const container = getContainer();
        if (container) resizeObserver.observe(container);
        reducedMotionQuery.addEventListener("change", () => {
            if (state.data && state.els) render();
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") hideTooltip();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    window.EventTrackerHeatmap = { load, clearFilter };
})();
