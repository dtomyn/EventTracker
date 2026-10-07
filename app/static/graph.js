/*
 * Shared force-graph engine for the Connection Graph and Tag Clusters pages.
 *
 * Exposes window.EventGraph.create(shell, options). All user-provided text is
 * written with textContent / d3 .text(); never innerHTML.
 */
(function () {
    "use strict";

    const SVG_NS = "http://www.w3.org/2000/svg";
    const DAY_MS = 86400000;
    const LABEL_MAX_CHARS = 34;
    const TIMELAPSE_DURATION_MS = 12000;

    function prefersReducedMotion() {
        return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    }

    function truncate(text, maxLength) {
        const value = String(text || "");
        return value.length > maxLength ? value.slice(0, maxLength - 1).trimEnd() + "…" : value;
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function endpointId(value) {
        return typeof value === "object" && value !== null ? value.id : value;
    }

    function parseIsoDay(value) {
        if (!value) return null;
        const parts = String(value).split("-").map(Number);
        if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
        return Math.round(Date.UTC(parts[0], parts[1] - 1, parts[2]) / DAY_MS);
    }

    const DATE_FORMAT = new Intl.DateTimeFormat(undefined, {
        year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
    });

    function formatDay(dayNumber) {
        return DATE_FORMAT.format(new Date(dayNumber * DAY_MS));
    }

    /**
     * @param {HTMLElement} shell  The .eg-shell element.
     * @param {object} options
     */
    function create(shell, options) {
        const opts = Object.assign({
            nodeRadius: () => 14,
            nodeColor: () => "#7c5cff",
            nodeLabel: (d) => d.label || "",
            nodeAriaLabel: (d) => d.label || "",
            linkKind: () => "explicit",
            linkWidth: () => 1.6,
            linkColor: null,
            linkDistance: 110,
            charge: -260,
            renderPanel: null,
            onActivate: null,
            timeOf: null,
            emptyMessage: "Nothing to show yet.",
        }, options || {});

        const stage = shell;
        const statusEl = shell.querySelector("[data-eg-status]");
        const statusText = shell.querySelector("[data-eg-status-text]");
        const statusSpinner = shell.querySelector("[data-eg-status-spinner]");
        const panel = shell.querySelector("[data-eg-panel]");
        const panelTitle = shell.querySelector("[data-eg-panel-title]");
        const panelKicker = shell.querySelector("[data-eg-panel-kicker]");
        const panelBody = shell.querySelector("[data-eg-panel-body]");
        const panelFooter = shell.querySelector("[data-eg-panel-footer]");
        const panelClose = shell.querySelector("[data-eg-panel-close]");
        const zoomButtons = Array.from(shell.querySelectorAll("[data-eg-zoom]"));
        const timelapse = shell.querySelector("[data-eg-timelapse]");
        const playButton = shell.querySelector("[data-eg-play]");
        const scrubber = shell.querySelector("[data-eg-scrub]");
        const dateOutput = shell.querySelector("[data-eg-date]");
        const countOutput = shell.querySelector("[data-eg-count]");

        if (typeof window.d3 === "undefined") {
            return createFallback(shell, opts);
        }

        const svgElement = document.createElementNS(SVG_NS, "svg");
        svgElement.setAttribute("class", "eg-svg");
        svgElement.setAttribute("role", "group");
        svgElement.setAttribute("aria-label", shell.getAttribute("data-eg-label") || "Graph");
        shell.insertBefore(svgElement, shell.firstChild);

        const svg = d3.select(svgElement);
        let state = null;
        let resizeObserver = null;
        let returnFocusTo = null;

        function setStatus(message, kind) {
            if (!statusEl) return;
            if (!message) {
                statusEl.hidden = true;
                return;
            }
            statusEl.hidden = false;
            statusEl.classList.toggle("is-error", kind === "error");
            if (statusSpinner) statusSpinner.hidden = kind !== "loading";
            if (statusText) statusText.textContent = message;
        }

        function setControlsDisabled(disabled) {
            zoomButtons.forEach((button) => { button.disabled = disabled; });
        }

        function size() {
            return { width: stage.clientWidth || 800, height: stage.clientHeight || 600 };
        }

        function insets() {
            const result = { top: 64, right: 72, bottom: 40, left: 40 };
            if (timelapse && !timelapse.hidden) {
                result.bottom = timelapse.offsetHeight + 40;
            }
            if (window.innerWidth < 768) {
                result.top = 72;
                result.right = 24;
                result.left = 24;
            }
            return result;
        }

        function panelInset() {
            if (!panel || !panel.classList.contains("is-open")) return { right: 0, bottom: 0 };
            if (window.innerWidth < 768) return { right: 0, bottom: panel.offsetHeight };
            return { right: panel.offsetWidth, bottom: 0 };
        }

        // ------------------------------------------------------------------
        // Rendering
        // ------------------------------------------------------------------

        function destroy() {
            if (state) {
                state.simulation.stop();
                if (state.playFrame) cancelAnimationFrame(state.playFrame);
            }
            svg.selectAll("*").remove();
            svg.on(".zoom", null).on("click", null);
            state = null;
        }

        function render(data) {
            closePanel(false);
            destroy();
            const nodes = (data && data.nodes) || [];
            const edges = (data && data.edges) || [];

            if (nodes.length === 0) {
                setControlsDisabled(true);
                if (timelapse) timelapse.hidden = true;
                setStatus(opts.emptyMessage, "empty");
                shell.dataset.egReady = "empty";
                return;
            }

            setStatus(null);
            setControlsDisabled(false);

            const nodeById = new Map(nodes.map((d) => [d.id, d]));
            const links = edges
                .filter((e) => nodeById.has(endpointId(e.source)) && nodeById.has(endpointId(e.target)))
                .map((e) => Object.assign({}, e));

            const adjacency = new Map(nodes.map((d) => [d.id, new Set()]));
            links.forEach((e) => {
                const s = endpointId(e.source);
                const t = endpointId(e.target);
                adjacency.get(s).add(t);
                adjacency.get(t).add(s);
            });

            nodes.forEach((d) => {
                d.r = opts.nodeRadius(d);
                d.color = opts.nodeColor(d);
                d.labelText = truncate(opts.nodeLabel(d), LABEL_MAX_CHARS);
                d.time = opts.timeOf ? opts.timeOf(d) : null;
            });

            const { width, height } = size();

            // Deterministic initial placement keeps layouts stable between loads.
            nodes.forEach((d, i) => {
                const angle = i * 2.399963;
                const radius = 18 * Math.sqrt(i + 1);
                d.x = width / 2 + radius * Math.cos(angle);
                d.y = height / 2 + radius * Math.sin(angle);
            });

            const simulation = d3.forceSimulation(nodes)
                .force("link", d3.forceLink(links).id((d) => d.id)
                    .distance((e) => (typeof opts.linkDistance === "function" ? opts.linkDistance(e) : opts.linkDistance))
                    .strength((e) => 0.25 + 0.5 * (e.weight || 0.5)))
                .force("charge", d3.forceManyBody().strength((d) => opts.charge - d.r * 6).distanceMax(900))
                .force("x", d3.forceX(width / 2).strength(0.06))
                .force("y", d3.forceY(height / 2).strength(0.08))
                .force("collide", d3.forceCollide().radius((d) => d.r + 14).iterations(3))
                .stop();
            simulation.tick(320);

            // ----- defs: glow filter + per-color radial gradients -----
            const defs = svg.append("defs");
            const glow = defs.append("filter")
                .attr("id", "eg-glow")
                .attr("x", "-100%").attr("y", "-100%")
                .attr("width", "300%").attr("height", "300%");
            glow.append("feGaussianBlur").attr("stdDeviation", 7);

            const colors = Array.from(new Set(nodes.map((d) => d.color)));
            const gradientId = new Map();
            colors.forEach((color, index) => {
                const id = `eg-grad-${index}`;
                gradientId.set(color, id);
                const base = d3.color(color) || d3.color("#7c5cff");
                const gradient = defs.append("radialGradient")
                    .attr("id", id).attr("cx", "35%").attr("cy", "30%").attr("r", "75%");
                gradient.append("stop").attr("offset", "0%").attr("stop-color", base.brighter(1.1).formatHex());
                gradient.append("stop").attr("offset", "55%").attr("stop-color", base.formatHex());
                gradient.append("stop").attr("offset", "100%").attr("stop-color", base.darker(0.7).formatHex());
            });

            const zoomLayer = svg.append("g").attr("class", "eg-zoom-layer");
            const linkLayer = zoomLayer.append("g").attr("class", "eg-links");
            const nodeLayer = zoomLayer.append("g").attr("class", "eg-nodes");
            const labelLayer = svg.append("g").attr("class", "eg-labels").attr("aria-hidden", "true");

            const linkSel = linkLayer.selectAll("path")
                .data(links)
                .join("path")
                .attr("class", (e) => `eg-link eg-link--${opts.linkKind(e)}`)
                .attr("stroke-width", (e) => opts.linkWidth(e))
                .attr("stroke", (e) => (opts.linkColor ? opts.linkColor(e) : null));
            linkSel.append("title").text((e) => e.note || "");

            const nodeSel = nodeLayer.selectAll("g.eg-node")
                .data(nodes, (d) => d.id)
                .join("g")
                .attr("class", "eg-node")
                .attr("tabindex", 0)
                .attr("role", "button")
                .attr("data-node-id", (d) => d.id)
                .attr("aria-label", (d) => opts.nodeAriaLabel(d));

            const inner = nodeSel.append("g").attr("class", "eg-node-inner");
            inner.append("circle")
                .attr("class", "eg-halo")
                .attr("r", (d) => d.r * 1.55)
                .attr("fill", (d) => d.color)
                .attr("filter", "url(#eg-glow)");
            inner.append("circle")
                .attr("class", "eg-core")
                .attr("r", (d) => d.r)
                .attr("fill", (d) => `url(#${gradientId.get(d.color)})`);
            inner.append("circle")
                .attr("class", "eg-ring")
                .attr("r", (d) => d.r + 5);

            const labelSel = labelLayer.selectAll("text")
                .data(nodes, (d) => d.id)
                .join("text")
                .attr("class", "eg-label")
                .attr("text-anchor", "middle")
                .text((d) => d.labelText);

            // Measure label widths once (at the strong size, a safe upper bound).
            labelSel.each(function (d) {
                this.classList.add("is-strong");
                d.labelWidth = this.getComputedTextLength() + 8;
                this.classList.remove("is-strong");
            });

            state = {
                nodes, links, nodeById, adjacency, simulation,
                zoomLayer, linkSel, nodeSel, labelSel,
                transform: d3.zoomIdentity,
                focusId: null, selectedId: null,
                visibleIds: new Set(nodes.map((d) => d.id)),
                labelFrame: 0, playFrame: 0, userZoomed: false,
            };

            // ----- zoom -----
            const zoom = d3.zoom()
                .scaleExtent([0.2, 5])
                .on("zoom", (event) => {
                    state.transform = event.transform;
                    zoomLayer.attr("transform", event.transform);
                    positionLabels();
                    scheduleLabelLayout();
                    if (event.sourceEvent) state.userZoomed = true;
                });
            state.zoom = zoom;
            svg.call(zoom).on("dblclick.zoom", null);

            svg.on("click", (event) => {
                if (event.target === svgElement) {
                    closePanel(true);
                }
            });

            // ----- interactions -----
            nodeSel
                .on("pointerenter", (event, d) => setFocus(d.id))
                .on("pointerleave", () => setFocus(state.selectedId))
                .on("focus", (event, d) => setFocus(d.id))
                .on("blur", () => setFocus(state.selectedId))
                .on("click", (event, d) => {
                    event.stopPropagation();
                    selectNode(d.id, event.currentTarget);
                })
                .on("dblclick", (event, d) => {
                    event.stopPropagation();
                    if (opts.onActivate) opts.onActivate(d);
                })
                .on("keydown", (event, d) => {
                    if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        selectNode(d.id, event.currentTarget);
                    }
                })
                .call(d3.drag()
                    .clickDistance(4)
                    .on("start", (event) => {
                        if (!event.active) simulation.alphaTarget(0.18).restart();
                        event.subject.fx = event.subject.x;
                        event.subject.fy = event.subject.y;
                    })
                    .on("drag", (event) => {
                        event.subject.fx = event.x;
                        event.subject.fy = event.y;
                    })
                    .on("end", (event) => {
                        if (!event.active) simulation.alphaTarget(0);
                        event.subject.fx = null;
                        event.subject.fy = null;
                    }));

            simulation.on("tick", () => {
                updatePositions();
                scheduleLabelLayout();
            });

            updatePositions();
            fit(0);
            playEntrance();
            setupTimelapse();
            shell.dataset.egReady = "true";
        }

        function linkPath(e) {
            const sx = e.source.x, sy = e.source.y, tx = e.target.x, ty = e.target.y;
            const dx = tx - sx, dy = ty - sy;
            const bend = 0.12;
            const cx = (sx + tx) / 2 - dy * bend;
            const cy = (sy + ty) / 2 + dx * bend;
            return `M${sx},${sy}Q${cx},${cy} ${tx},${ty}`;
        }

        function updatePositions() {
            if (!state) return;
            state.linkSel.attr("d", linkPath);
            state.nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);
            positionLabels();
        }

        function positionLabels() {
            if (!state) return;
            const t = state.transform;
            state.labelSel
                .attr("x", (d) => t.applyX(d.x) + (d.labelDx || 0))
                .attr("y", (d) => t.applyY(d.y) + (d.labelTop === undefined ? d.r * t.k + 4 : d.labelTop) + 12.5);
        }

        function scheduleLabelLayout() {
            if (!state || state.labelFrame) return;
            state.labelFrame = requestAnimationFrame(() => {
                if (!state) return;
                state.labelFrame = 0;
                layoutLabels();
            });
        }

        /**
         * Greedy collision avoidance in screen space: higher priority labels
         * claim space first; anything that would overlap is hidden.
         */
        function layoutLabels() {
            const t = state.transform;
            const focusSet = focusMembers();
            const candidates = state.nodes.filter((d) => {
                if (!state.visibleIds.has(d.id)) return false;
                if (focusSet && !focusSet.has(d.id)) return false;
                return true;
            });

            const priority = (d) => {
                if (d.id === state.focusId || d.id === state.selectedId) return 1e6;
                if (focusSet) return 1e5 + d.r;
                return d.r;
            };
            candidates.sort((a, b) => priority(b) - priority(a));

            const { width, height } = size();
            const placed = overlayObstacles();
            const nodeBoxes = new Map();
            state.nodes.forEach((d) => {
                if (!state.visibleIds.has(d.id)) return;
                const sx = t.applyX(d.x);
                const sy = t.applyY(d.y);
                const sr = d.r * t.k + 2;
                nodeBoxes.set(d.id, { x1: sx - sr, x2: sx + sr, y1: sy - sr, y2: sy + sr });
            });
            const overlaps = (a, b) => a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
            const shown = new Set();
            const strong = new Set();
            const minScreenRadius = 9;
            const edge = 6;

            const lineHeight = 17;
            const isFree = (box, ownId) => {
                if (box.x1 < edge || box.x2 > width - edge || box.y1 < 2 || box.y2 > height - 2) return false;
                if (placed.some((p) => overlaps(box, p))) return false;
                for (const [id, nodeBox] of nodeBoxes) {
                    if (id !== ownId && overlaps(box, nodeBox)) return false;
                }
                return true;
            };

            candidates.forEach((d) => {
                const screenR = d.r * t.k;
                const forced = priority(d) >= 1e5;
                const pinned = d.id === state.focusId || d.id === state.selectedId;
                if (!forced && screenR < minScreenRadius && t.k < 1.6) return;
                const sx = t.applyX(d.x);
                const sy = t.applyY(d.y);
                if (sx < -screenR || sx > width + screenR || sy < -screenR || sy > height + screenR) return;
                const half = d.labelWidth / 2;
                // Candidate slots: below, above, right, left (offsets of the label centre / top).
                const slots = [
                    { dx: 0, top: screenR + 4 },
                    { dx: 0, top: -screenR - 4 - lineHeight },
                    { dx: screenR + 6 + half, top: -lineHeight / 2 },
                    { dx: -(screenR + 6 + half), top: -lineHeight / 2 },
                ];
                let chosen = null;
                for (const slot of slots) {
                    const cx = sx + slot.dx;
                    const box = { x1: cx - half, x2: cx + half, y1: sy + slot.top, y2: sy + slot.top + lineHeight };
                    if (isFree(box, d.id)) { chosen = { slot, box }; break; }
                }
                if (!chosen && pinned) {
                    // Always show the focused label; keep it on screen.
                    const cx = Math.min(Math.max(sx, edge + half), width - edge - half);
                    const slot = { dx: cx - sx, top: screenR + 4 };
                    chosen = { slot, box: { x1: cx - half, x2: cx + half, y1: sy + slot.top, y2: sy + slot.top + lineHeight } };
                }
                if (!chosen) return;
                d.labelDx = chosen.slot.dx;
                d.labelTop = chosen.slot.top;
                placed.push(chosen.box);
                shown.add(d.id);
                if (forced) strong.add(d.id);
            });

            state.labelSel
                .classed("is-visible", (d) => shown.has(d.id))
                .classed("is-strong", (d) => strong.has(d.id));
            positionLabels();
        }

        /** Screen boxes of the floating controls so labels never hide under them. */
        function overlayObstacles() {
            const stageRect = stage.getBoundingClientRect();
            const boxes = [];
            shell.querySelectorAll("[data-eg-legend], .eg-zoom, [data-eg-timelapse], [data-eg-panel].is-open").forEach((node) => {
                if (node.hidden || node.offsetParent === null) return;
                const r = node.getBoundingClientRect();
                boxes.push({
                    x1: r.left - stageRect.left - 4,
                    x2: r.right - stageRect.left + 4,
                    y1: r.top - stageRect.top - 4,
                    y2: r.bottom - stageRect.top + 4,
                });
            });
            return boxes;
        }

        function focusMembers() {
            if (state.focusId === null || state.focusId === undefined) return null;
            const set = new Set([state.focusId]);
            (state.adjacency.get(state.focusId) || new Set()).forEach((id) => {
                if (state.visibleIds.has(id)) set.add(id);
            });
            return set;
        }

        function setFocus(id) {
            if (!state) return;
            state.focusId = id === undefined ? null : id;
            const members = focusMembers();
            svg.classed("eg-has-focus", !!members);
            state.nodeSel
                .classed("is-focus", (d) => !!members && members.has(d.id))
                .classed("is-hovered", (d) => d.id === state.focusId && d.id !== state.selectedId);
            state.linkSel.classed("is-focus", (e) => !!members
                && (e.source.id === state.focusId || e.target.id === state.focusId));
            layoutLabels();
        }

        // ------------------------------------------------------------------
        // Zoom helpers
        // ------------------------------------------------------------------

        function transition(duration) {
            return prefersReducedMotion() || duration === 0
                ? svg
                : svg.transition().duration(duration).ease(d3.easeCubicInOut);
        }

        function fit(duration) {
            if (!state) return;
            const visible = state.nodes.filter((d) => state.visibleIds.has(d.id));
            const pool = visible.length ? visible : state.nodes;
            const base = insets();
            const candidates = [base];
            // The legend sits in the top-left corner. Try keeping the graph
            // either beside it or below it and use whichever fits larger.
            const legend = shell.querySelector("[data-eg-legend]");
            if (legend && legend.offsetParent !== null) {
                const stageRect = stage.getBoundingClientRect();
                const rect = legend.getBoundingClientRect();
                candidates.length = 0;
                candidates.push(Object.assign({}, base, { left: Math.max(base.left, rect.right - stageRect.left + 16) }));
                candidates.push(Object.assign({}, base, { top: Math.max(base.top, rect.bottom - stageRect.top + 12) }));
            }
            const best = candidates
                .map((pad) => computeFit(pool, pad))
                .reduce((a, b) => (b.k > a.k ? b : a));
            transition(duration === undefined ? 600 : duration).call(state.zoom.transform, best.target);
            state.userZoomed = false;
        }

        function computeFit(pool, pad) {
            const { width, height } = size();
            const side = panelInset();
            const x0 = d3.min(pool, (d) => d.x - Math.max(d.r, Math.min(d.labelWidth || 0, 160) / 2));
            const x1 = d3.max(pool, (d) => d.x + Math.max(d.r, Math.min(d.labelWidth || 0, 160) / 2));
            const y0 = d3.min(pool, (d) => d.y - d.r);
            const y1 = d3.max(pool, (d) => d.y + d.r + 22);
            const availW = Math.max(120, width - pad.left - pad.right - side.right);
            const availH = Math.max(120, height - pad.top - pad.bottom - side.bottom);
            const k = Math.min(1.8, availW / Math.max(1, x1 - x0), availH / Math.max(1, y1 - y0));
            const target = d3.zoomIdentity
                .translate(pad.left + availW / 2, pad.top + availH / 2)
                .scale(k)
                .translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
            return { k, target };
        }

        function zoomBy(factor) {
            if (!state) return;
            transition(220).call(state.zoom.scaleBy, factor);
        }

        function centerOn(d) {
            const { width, height } = size();
            const side = panelInset();
            const k = Math.min(2.4, Math.max(state.transform.k, 1.35));
            const cx = (width - side.right) / 2;
            const cy = (height - side.bottom) / 2;
            const target = d3.zoomIdentity.translate(cx, cy).scale(k).translate(-d.x, -d.y);
            transition(650).call(state.zoom.transform, target);
        }

        zoomButtons.forEach((button) => {
            button.addEventListener("click", () => {
                const action = button.getAttribute("data-eg-zoom");
                if (action === "in") zoomBy(1.35);
                else if (action === "out") zoomBy(1 / 1.35);
                else fit(600);
            });
        });

        // ------------------------------------------------------------------
        // Entrance animation
        // ------------------------------------------------------------------

        function playEntrance() {
            if (prefersReducedMotion()) return;
            const { width, height } = size();
            const t = state.transform;
            const cx = width / 2, cy = height / 2;
            let maxDelay = 0;
            state.nodeSel.each(function (d) {
                const dist = Math.hypot(t.applyX(d.x) - cx, t.applyY(d.y) - cy);
                const delay = Math.round(Math.min(700, dist * 1.4));
                maxDelay = Math.max(maxDelay, delay);
                this.style.setProperty("--eg-delay", `${delay}ms`);
                this.classList.add("is-entering");
            });
            const linkLayer = svgElement.querySelector(".eg-links");
            linkLayer.style.setProperty("--eg-delay", `${Math.round(maxDelay * 0.5)}ms`);
            linkLayer.classList.add("is-entering");
            window.setTimeout(() => {
                if (!state) return;
                state.nodeSel.classed("is-entering", false);
                linkLayer.classList.remove("is-entering");
            }, maxDelay + 900);
        }

        // ------------------------------------------------------------------
        // Side panel
        // ------------------------------------------------------------------

        function selectNode(id, sourceElement) {
            if (!state) return;
            const d = state.nodeById.get(id);
            if (!d) return;
            state.selectedId = id;
            returnFocusTo = sourceElement || svgElement.querySelector(`[data-node-id="${CSS.escape(String(id))}"]`);
            state.nodeSel.classed("is-selected", (n) => n.id === id);
            setFocus(id);
            openPanel(d);
            centerOn(d);
        }

        function openPanel(d) {
            if (!panel || !opts.renderPanel) return;
            panelBody.replaceChildren();
            if (panelFooter) panelFooter.replaceChildren();
            const api = {
                el,
                neighbors: () => Array.from(state.adjacency.get(d.id) || [])
                    .map((nid) => state.nodeById.get(nid))
                    .filter(Boolean),
                linksOf: () => state.links.filter((e) => e.source.id === d.id || e.target.id === d.id),
                select: (nid) => selectNode(nid),
                setTitle: (text) => { panelTitle.textContent = text; },
                setKicker: (text, color) => {
                    panelKicker.replaceChildren();
                    if (color) {
                        const dot = el("span", "eg-dot");
                        dot.style.background = color;
                        dot.style.color = color;
                        panelKicker.appendChild(dot);
                    }
                    panelKicker.appendChild(document.createTextNode(text));
                },
                body: panelBody,
                footer: panelFooter,
            };
            opts.renderPanel(d, api);
            panelBody.scrollTop = 0;
            const wasOpen = panel.classList.contains("is-open");
            panel.classList.add("is-open");
            panel.removeAttribute("inert");
            panel.setAttribute("aria-hidden", "false");
            shell.classList.add("has-panel");
            if (!wasOpen && panelClose) {
                window.setTimeout(() => panelClose.focus({ preventScroll: true }), 30);
            }
        }

        function closePanel(restoreFocus) {
            if (!panel) return;
            const wasOpen = panel.classList.contains("is-open");
            panel.classList.remove("is-open");
            panel.setAttribute("inert", "");
            panel.setAttribute("aria-hidden", "true");
            shell.classList.remove("has-panel");
            if (state) {
                state.selectedId = null;
                state.nodeSel.classed("is-selected", false);
                setFocus(null);
            }
            if (wasOpen && restoreFocus && returnFocusTo && document.contains(returnFocusTo)) {
                returnFocusTo.focus({ preventScroll: true });
            }
        }

        if (panelClose) panelClose.addEventListener("click", () => closePanel(true));
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && panel && panel.classList.contains("is-open")) {
                event.preventDefault();
                closePanel(true);
            }
        });

        // ------------------------------------------------------------------
        // Time-lapse
        // ------------------------------------------------------------------

        function setupTimelapse() {
            if (!timelapse || !opts.timeOf) return;
            const times = state.nodes.map((d) => d.time).filter((t) => t !== null && t !== undefined);
            if (times.length < 2 || d3.min(times) === d3.max(times)) {
                timelapse.hidden = true;
                return;
            }
            state.minTime = d3.min(times);
            state.maxTime = d3.max(times);
            timelapse.hidden = false;
            scrubber.min = "0";
            scrubber.max = String(state.maxTime - state.minTime);
            scrubber.step = "1";
            const reduced = prefersReducedMotion();
            playButton.disabled = reduced;
            playButton.title = reduced ? "Time-lapse playback is off because reduced motion is enabled. Use the slider instead." : "";
            setTime(state.maxTime, false);
            // The bar changes the available height, so refit without animation.
            fit(0);
        }

        function setTime(dayNumber, animateBirths) {
            if (!state || state.minTime === undefined) return;
            const t = Math.max(state.minTime, Math.min(state.maxTime, dayNumber));
            state.currentTime = t;
            const previous = state.visibleIds;
            const next = new Set(state.nodes
                .filter((d) => d.time === null || d.time === undefined || d.time <= t)
                .map((d) => d.id));
            state.visibleIds = next;

            const allowBirth = animateBirths && !prefersReducedMotion();
            state.nodeSel.each(function (d) {
                const visible = next.has(d.id);
                this.classList.toggle("is-hidden", !visible);
                this.setAttribute("aria-hidden", visible ? "false" : "true");
                this.setAttribute("tabindex", visible ? "0" : "-1");
                if (allowBirth && visible && !previous.has(d.id)) {
                    this.classList.remove("is-born");
                    void this.getBoundingClientRect();
                    this.classList.add("is-born");
                }
            });
            state.linkSel.classed("is-hidden", (e) => !(next.has(e.source.id) && next.has(e.target.id)));

            if (state.selectedId !== null && !next.has(state.selectedId)) closePanel(false);

            const span = state.maxTime - state.minTime;
            const offset = t - state.minTime;
            scrubber.value = String(offset);
            scrubber.style.setProperty("--eg-progress", `${span ? (offset / span) * 100 : 100}%`);
            const label = formatDay(t);
            dateOutput.textContent = label;
            scrubber.setAttribute("aria-valuetext", label);
            countOutput.textContent = `${next.size} of ${state.nodes.length} entries`;
            shell.dataset.egVisibleCount = String(next.size);
            layoutLabels();
        }

        function setPlaying(playing) {
            if (!state) return;
            state.playing = playing;
            playButton.classList.toggle("is-playing", playing);
            playButton.setAttribute("aria-label", playing ? "Pause time-lapse" : "Play time-lapse");
            playButton.setAttribute("aria-pressed", playing ? "true" : "false");
            shell.dataset.egPlaying = playing ? "true" : "false";
            if (state.playFrame) {
                cancelAnimationFrame(state.playFrame);
                state.playFrame = 0;
            }
            if (!playing) return;

            let start = state.currentTime;
            if (start >= state.maxTime) {
                start = state.minTime;
                setTime(start, true);
            }
            const span = state.maxTime - state.minTime;
            const duration = TIMELAPSE_DURATION_MS * ((state.maxTime - start) / span);
            const startedAt = performance.now();
            const step = (now) => {
                if (!state || !state.playing) return;
                const progress = Math.min(1, (now - startedAt) / Math.max(1, duration));
                const day = Math.round(start + (state.maxTime - start) * progress);
                if (day !== state.currentTime) setTime(day, true);
                if (progress >= 1) {
                    setPlaying(false);
                    return;
                }
                state.playFrame = requestAnimationFrame(step);
            };
            state.playFrame = requestAnimationFrame(step);
        }

        if (playButton) {
            playButton.addEventListener("click", () => {
                if (!state || prefersReducedMotion()) return;
                setPlaying(!state.playing);
            });
        }

        if (scrubber) {
            scrubber.addEventListener("input", () => {
                if (!state || state.minTime === undefined) return;
                if (state.playing) setPlaying(false);
                setTime(state.minTime + Number(scrubber.value), false);
            });
        }

        // ------------------------------------------------------------------
        // Resize
        // ------------------------------------------------------------------

        if (window.ResizeObserver) {
            let lastWidth = 0, lastHeight = 0;
            resizeObserver = new ResizeObserver(() => {
                const { width, height } = size();
                if (Math.abs(width - lastWidth) < 2 && Math.abs(height - lastHeight) < 2) return;
                lastWidth = width;
                lastHeight = height;
                if (state && !state.userZoomed && !state.selectedId) fit(0);
                else if (state) layoutLabels();
            });
            resizeObserver.observe(stage);
        }

        return {
            render,
            setStatus,
            setControlsDisabled,
            fit: () => fit(600),
            select: (id) => selectNode(id),
            close: () => closePanel(true),
            loading(message) {
                closePanel(false);
                destroy();
                if (timelapse) timelapse.hidden = true;
                setControlsDisabled(true);
                delete shell.dataset.egReady;
                setStatus(message, "loading");
            },
            error(message) {
                destroy();
                if (timelapse) timelapse.hidden = true;
                setControlsDisabled(true);
                setStatus(message, "error");
                shell.dataset.egReady = "error";
            },
        };
    }

    /**
     * Controller used when D3 could not be loaded (offline, CDN blocked). It
     * still reports empty and error states so the page never looks stuck.
     */
    function createFallback(shell, opts) {
        const statusEl = shell.querySelector("[data-eg-status]");
        const statusText = shell.querySelector("[data-eg-status-text]");
        const statusSpinner = shell.querySelector("[data-eg-status-spinner]");
        function setStatus(message, kind) {
            if (!statusEl) return;
            statusEl.hidden = !message;
            statusEl.classList.toggle("is-error", kind === "error");
            if (statusSpinner) statusSpinner.hidden = kind !== "loading";
            if (statusText && message) statusText.textContent = message;
        }
        return {
            render(data) {
                const empty = !data || !data.nodes || data.nodes.length === 0;
                setStatus(
                    empty ? opts.emptyMessage : "The graph library could not be loaded. Check your network connection and reload.",
                    empty ? "empty" : "error",
                );
                shell.dataset.egReady = empty ? "empty" : "error";
            },
            setStatus,
            setControlsDisabled() {},
            fit() {},
            select() {},
            close() {},
            loading(message) { setStatus(message, "loading"); },
            error(message) {
                setStatus(message, "error");
                shell.dataset.egReady = "error";
            },
        };
    }

    window.EventGraph = { create, el, truncate, parseIsoDay, formatDay, prefersReducedMotion };
})();
