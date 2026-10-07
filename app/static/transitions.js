// Cross-document view transitions: entry card <-> entry detail morph.
(function () {
    'use strict';

    if (!('onpagereveal' in window) || !window.CSS || !CSS.supports('view-transition-class', 'a')) {
        return;
    }

    var STORAGE_KEY = 'eventtracker:vt-entry';
    var DETAIL_PATH = /^\/entries\/(\d+)\/view\/?$/;
    // Pages with heavy canvases or full-screen layouts navigate without transitions.
    var EXCLUDED_PATH = /^\/(?:story\/\d+\/presentation|timeline\/board)(?:\/|$)/;
    var CARD_SELECTOR = '[data-entry-card], article, .card, li, .list-group-item';
    var DETAIL_SELECTOR = '[data-entry-transition-target]';
    var named = [];
    var pendingCard = null;

    function reducedMotion() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function parseUrl(value) {
        try {
            var url = new URL(value, window.location.href);
            return url.origin === window.location.origin ? url : null;
        } catch (err) {
            return null;
        }
    }

    function detailIdFrom(url) {
        var match = url ? DETAIL_PATH.exec(url.pathname) : null;
        return match ? match[1] : null;
    }

    function readState() {
        try {
            var state = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null');
            return state && /^\d+$/.test(String(state.id)) ? state : null;
        } catch (err) {
            return null;
        }
    }

    function writeState(state) {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
        } catch (err) {
            // Storage can be unavailable; the morph is purely cosmetic.
        }
    }

    function clearNames() {
        named.forEach(function (el) {
            el.style.viewTransitionName = '';
            el.style.viewTransitionClass = '';
        });
        named = [];
    }

    // Only one dynamic name is ever assigned per document, so names stay unique.
    function assignName(el, id) {
        if (!el || !/^\d+$/.test(String(id))) {
            return false;
        }
        clearNames();
        el.style.viewTransitionName = 'entry-' + id;
        el.style.viewTransitionClass = 'entry-morph';
        named.push(el);
        return true;
    }

    function isVisible(el) {
        var rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight;
    }

    function cardForAnchor(anchor) {
        return anchor ? anchor.closest(CARD_SELECTOR) : null;
    }

    // Prefer a container of the same kind the morph started from (e.g. preview pane vs card).
    function findCardForEntry(id, tag) {
        var anchors = document.querySelectorAll('a[href="/entries/' + id + '/view"], a[href="/entries/' + id + '"]');
        var fallback = null;
        for (var i = 0; i < anchors.length; i += 1) {
            var card = cardForAnchor(anchors[i]);
            if (card && isVisible(card)) {
                if (!tag || card.tagName === tag) {
                    return card;
                }
                fallback = fallback || card;
            }
        }
        return fallback;
    }

    function clearWhenDone(transition) {
        if (transition && transition.finished) {
            transition.finished.then(clearNames, clearNames);
        } else {
            clearNames();
        }
    }

    document.addEventListener('click', function (event) {
        pendingCard = null;
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }
        var anchor = event.target instanceof Element ? event.target.closest('a[href^="/entries/"]') : null;
        if (!anchor || (anchor.target && anchor.target !== '_self') || document.querySelector(DETAIL_SELECTOR)) {
            return;
        }
        var id = detailIdFrom(parseUrl(anchor.getAttribute('href')));
        var card = id ? cardForAnchor(anchor) : null;
        if (card) {
            pendingCard = { id: id, el: card };
        }
    }, true);

    window.addEventListener('pageswap', function (event) {
        var transition = event.viewTransition;
        if (!transition) {
            return;
        }
        var activation = event.activation;
        var target = activation && activation.entry ? parseUrl(activation.entry.url) : null;
        if (reducedMotion() || !target || EXCLUDED_PATH.test(target.pathname) || EXCLUDED_PATH.test(window.location.pathname)) {
            pendingCard = null;
            transition.skipTransition();
            return;
        }

        var targetId = detailIdFrom(target);
        var currentId = detailIdFrom(window.location);
        var state = readState();
        if (targetId && pendingCard && pendingCard.id === targetId) {
            // Forward: timeline card -> detail page.
            if (assignName(pendingCard.el, targetId)) {
                writeState({ id: targetId, tag: pendingCard.el.tagName });
            }
        } else if (currentId && state && state.id === currentId && activation.navigationType === 'traverse') {
            // Back: detail page -> the card it came from.
            assignName(document.querySelector(DETAIL_SELECTOR), currentId);
        }
        pendingCard = null;
        clearWhenDone(transition);
    });

    window.addEventListener('pagereveal', function (event) {
        clearNames();
        var transition = event.viewTransition;
        if (!transition) {
            return;
        }
        if (reducedMotion()) {
            transition.skipTransition();
            return;
        }
        var activation = window.navigation ? window.navigation.activation : null;
        var from = activation && activation.from ? parseUrl(activation.from.url) : null;
        var state = readState();
        var currentId = detailIdFrom(window.location);
        if (state && currentId && currentId === state.id) {
            assignName(document.querySelector(DETAIL_SELECTOR), currentId);
        } else if (state && activation && activation.navigationType === 'traverse' && detailIdFrom(from) === state.id) {
            assignName(findCardForEntry(state.id, typeof state.tag === 'string' ? state.tag : ''), state.id);
        }
        clearWhenDone(transition);
    });

    // Restored from bfcache: never keep stale names around.
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            clearNames();
        }
    });
})();
