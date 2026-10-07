// Timeline page enhancements: sticky marker offset, reveal-on-scroll for the
// spine and count-up stat tiles. Everything here is progressive: without this
// script the page renders fully visible and static.
(() => {
    "use strict";

    const root = document.documentElement;
    const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const motionAllowed = !reducedMotionQuery.matches && "IntersectionObserver" in window;

    // Keep the sticky month marker just below the sticky navbar.
    const navbar = document.querySelector(".navbar.sticky-top");
    const syncStickyOffset = () => {
        if (!navbar) {
            return;
        }
        const height = Math.round(navbar.getBoundingClientRect().height);
        root.style.setProperty("--tl-sticky-top", `${height}px`);
    };
    syncStickyOffset();
    if (navbar && "ResizeObserver" in window) {
        new ResizeObserver(syncStickyOffset).observe(navbar);
    }

    // --- Count-up stat tiles ---------------------------------------------
    const animateCount = (node) => {
        const raw = node.getAttribute("data-tl-count") || "";
        const target = Number.parseFloat(raw);
        if (!Number.isFinite(target) || target <= 0) {
            return;
        }
        const decimals = raw.includes(".") ? raw.split(".")[1].length : 0;
        const duration = Math.min(1400, 600 + target * 4);
        const finalText = node.textContent;
        let startTime = 0;
        const step = (now) => {
            if (!startTime) {
                startTime = now;
            }
            const progress = Math.min(1, (now - startTime) / duration);
            const eased = 1 - Math.pow(1 - progress, 3);
            node.textContent = progress < 1 ? (target * eased).toFixed(decimals) : finalText;
            if (progress < 1) {
                window.requestAnimationFrame(step);
            }
        };
        node.textContent = (0).toFixed(decimals);
        window.requestAnimationFrame(step);
    };

    if (!motionAllowed) {
        return;
    }

    root.classList.add("tl-motion");
    document.querySelectorAll("[data-tl-count]").forEach(animateCount);

    // --- Reveal spine items as they enter the viewport -------------------
    const STAGGER_MS = 70;
    const MAX_STAGGER_STEPS = 5;
    let batch = [];
    let batchScheduled = false;

    const flushBatch = () => {
        batchScheduled = false;
        batch.forEach((item, index) => {
            item.style.setProperty("--tl-delay", `${Math.min(index, MAX_STAGGER_STEPS) * STAGGER_MS}ms`);
            item.classList.remove("tl-pending");
            item.addEventListener("transitionend", () => item.style.removeProperty("--tl-delay"), { once: true });
        });
        batch = [];
    };

    const observer = new IntersectionObserver((records) => {
        records.forEach((record) => {
            if (!record.isIntersecting) {
                return;
            }
            observer.unobserve(record.target);
            batch.push(record.target);
        });
        if (batch.length && !batchScheduled) {
            batchScheduled = true;
            window.requestAnimationFrame(flushBatch);
        }
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });

    const track = (scope) => {
        scope.querySelectorAll(".timeline-spine-item:not([data-tl-tracked])").forEach((item) => {
            item.setAttribute("data-tl-tracked", "");
            item.classList.add("tl-pending");
            observer.observe(item);
        });
    };

    const detailGroups = document.querySelector("[data-detail-groups]");
    if (!detailGroups) {
        return;
    }
    track(detailGroups);

    // Infinite scroll appends new groups/entries; pick them up as they land.
    new MutationObserver(() => track(detailGroups)).observe(detailGroups, { childList: true, subtree: true });
})();
