/* Cinematic Story Mode presentation controller (story_presentation.html).
 * All slide content is server-rendered; this script only toggles classes,
 * attributes, and textContent.
 */
(function () {
    'use strict';

    var body = document.body;
    var root = document.documentElement;
    var stage = document.querySelector('[data-cine-stage]');
    if (!body || !stage) {
        return;
    }

    var slides = Array.prototype.slice.call(stage.querySelectorAll('[data-cine-slide]'));
    var dots = Array.prototype.slice.call(document.querySelectorAll('[data-cine-dot]'));
    var counter = document.querySelector('[data-cine-counter]');
    var progress = document.querySelector('[data-cine-progress]');
    var progressFill = document.querySelector('[data-cine-progress-fill]');
    var autoplayFill = document.querySelector('[data-cine-autoplay-fill]');
    var autoplayBtn = document.querySelector('[data-cine-autoplay]');
    var themeBtn = document.querySelector('[data-cine-theme]');
    var fullscreenBtn = document.querySelector('[data-cine-fullscreen]');
    var restartBtn = document.querySelector('[data-cine-restart]');
    var storyUrl = body.getAttribute('data-story-url') || '/story';
    var total = slides.length;
    var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    var canHover = window.matchMedia('(hover: hover) and (pointer: fine)');

    var THEME_KEY = 'eventtracker-presentation-theme';
    var LEAVE_MS = 1150;
    var AUTOPLAY_BASE_MS = 6000;
    var AUTOPLAY_PER_WORD_MS = 160;
    var AUTOPLAY_MAX_MS = 22000;
    var IDLE_MS = 2800;

    var current = -1;
    var leaveTimers = [];
    var autoplayTimer = null;
    var idleTimer = null;
    var touchStart = null;

    if (!total) {
        return;
    }

    function clamp(index) {
        return Math.max(0, Math.min(total - 1, index));
    }

    function slideHue(slide, name, fallback) {
        var value = slide.style.getPropertyValue(name);
        return value ? value.trim() : fallback;
    }

    function indexFromHash() {
        var hash = window.location.hash.replace(/^#/, '');
        if (!hash) {
            return 0;
        }
        for (var i = 0; i < slides.length; i += 1) {
            if (slides[i].getAttribute('data-slide-name') === hash) {
                return i;
            }
        }
        var match = /^slide-(\d+)$/.exec(hash);
        if (match) {
            return clamp(parseInt(match[1], 10) - 1);
        }
        return 0;
    }

    function writeHash(index) {
        var name = slides[index].getAttribute('data-slide-name') || ('slide-' + (index + 1));
        var url = window.location.pathname + window.location.search + '#' + name;
        if (window.location.hash !== '#' + name) {
            window.history.replaceState(null, '', url);
        }
    }

    function show(index, options) {
        var target = clamp(index);
        var instant = Boolean(options && options.instant);
        if (target === current) {
            return;
        }
        var previous = current;
        stage.setAttribute('data-direction', target < previous ? 'backward' : 'forward');
        current = target;

        slides.forEach(function (slide, i) {
            var isActive = i === target;
            slide.classList.toggle('is-active', isActive);
            slide.setAttribute('aria-hidden', isActive ? 'false' : 'true');
            if ('inert' in slide) {
                slide.inert = !isActive;
            }
            if (isActive) {
                slide.classList.remove('is-leaving');
            }
        });

        if (previous >= 0 && !instant) {
            var leaving = slides[previous];
            leaving.classList.add('is-leaving');
            leaveTimers.push(window.setTimeout(function () {
                if (slides.indexOf(leaving) !== current) {
                    leaving.classList.remove('is-leaving');
                }
            }, reducedMotion.matches ? 300 : LEAVE_MS));
        }

        var activeSlide = slides[target];
        var scroller = activeSlide.querySelector('[data-cine-scroll]');
        if (scroller) {
            scroller.scrollTop = 0;
        }
        body.style.setProperty('--active-hue', slideHue(activeSlide, '--hue', '262'));
        body.style.setProperty('--active-hue-2', slideHue(activeSlide, '--hue-2', '200'));

        dots.forEach(function (dot, i) {
            dot.classList.toggle('is-active', i === target);
            dot.classList.toggle('is-visited', i < target);
            if (i === target) {
                dot.setAttribute('aria-current', 'step');
            } else {
                dot.removeAttribute('aria-current');
            }
        });
        if (counter) {
            counter.textContent = (target + 1) + ' / ' + total;
        }
        if (progress) {
            progress.setAttribute('aria-valuenow', String(target + 1));
        }
        if (progressFill) {
            progressFill.style.width = (total > 1 ? (target / (total - 1)) * 100 : 100) + '%';
        }
        body.setAttribute('data-current-slide', String(target));
        writeHash(target);
        scheduleAutoplay();
    }

    function next() {
        show(current + 1);
    }

    function prev() {
        show(current - 1);
    }

    function exitPresentation() {
        window.location.assign(storyUrl);
    }

    /* ---------- autoplay ---------- */
    function slideDuration(slide) {
        var words = (slide.textContent || '').trim().split(/\s+/).length;
        return Math.min(AUTOPLAY_MAX_MS, AUTOPLAY_BASE_MS + words * AUTOPLAY_PER_WORD_MS);
    }

    function restartAutoplayFill(duration) {
        if (!autoplayFill) {
            return;
        }
        body.style.setProperty('--cine-autoplay-ms', duration + 'ms');
        autoplayFill.style.animation = 'none';
        void autoplayFill.offsetWidth;
        autoplayFill.style.animation = '';
    }

    function scheduleAutoplay() {
        window.clearTimeout(autoplayTimer);
        autoplayTimer = null;
        if (!body.classList.contains('is-autoplaying')) {
            return;
        }
        if (current >= total - 1) {
            setAutoplay(false);
            return;
        }
        var duration = slideDuration(slides[current]);
        restartAutoplayFill(duration);
        autoplayTimer = window.setTimeout(next, duration);
    }

    function setAutoplay(enabled) {
        body.classList.toggle('is-autoplaying', enabled);
        if (autoplayBtn) {
            autoplayBtn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
            autoplayBtn.setAttribute('aria-label', enabled ? 'Pause autoplay' : 'Start autoplay');
            autoplayBtn.setAttribute('title', enabled ? 'Pause autoplay (P)' : 'Autoplay (P)');
        }
        if (enabled && current >= total - 1) {
            show(0);
            return;
        }
        scheduleAutoplay();
    }

    function toggleAutoplay() {
        setAutoplay(!body.classList.contains('is-autoplaying'));
    }

    /* ---------- theme ---------- */
    function toggleTheme() {
        var nextTheme = root.getAttribute('data-presentation-theme') === 'light' ? 'dark' : 'light';
        root.setAttribute('data-presentation-theme', nextTheme);
        try {
            localStorage.setItem(THEME_KEY, nextTheme);
        } catch (error) {
            /* storage unavailable */
        }
    }

    /* ---------- fullscreen ---------- */
    function fullscreenElement() {
        return document.fullscreenElement || document.webkitFullscreenElement || null;
    }

    function toggleFullscreen() {
        if (fullscreenElement()) {
            var exit = document.exitFullscreen || document.webkitExitFullscreen;
            if (exit) {
                Promise.resolve(exit.call(document)).catch(function () {});
            }
            return;
        }
        var request = root.requestFullscreen || root.webkitRequestFullscreen;
        if (request) {
            Promise.resolve(request.call(root)).catch(function () {});
        }
    }

    function syncFullscreen() {
        var active = Boolean(fullscreenElement());
        body.classList.toggle('is-fullscreen', active);
        if (fullscreenBtn) {
            fullscreenBtn.setAttribute('aria-pressed', active ? 'true' : 'false');
            fullscreenBtn.setAttribute('aria-label', active ? 'Exit fullscreen' : 'Enter fullscreen');
        }
    }

    if (fullscreenBtn && !(root.requestFullscreen || root.webkitRequestFullscreen)) {
        fullscreenBtn.hidden = true;
    }

    /* ---------- idle chrome ---------- */
    function wake() {
        body.classList.remove('is-idle');
        window.clearTimeout(idleTimer);
        if (!canHover.matches) {
            return;
        }
        idleTimer = window.setTimeout(function () {
            if (!document.querySelector('.cine-topbar:hover, .cine-dots:hover, .cine-topbar :focus-visible')) {
                body.classList.add('is-idle');
            }
        }, IDLE_MS);
    }

    /* ---------- input ---------- */
    function isInteractive(target) {
        return Boolean(target && target.closest && target.closest('a, button, input, textarea, select, [data-cine-scroll] *, .cine-cites'));
    }

    document.addEventListener('keydown', function (event) {
        if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) {
            return;
        }
        var key = event.key;
        var onControl = event.target && event.target.closest && event.target.closest('button, a');
        if (key === 'ArrowRight' || key === 'PageDown' || (key === ' ' && !onControl)) {
            event.preventDefault();
            next();
        } else if (key === 'ArrowLeft' || key === 'PageUp') {
            event.preventDefault();
            prev();
        } else if (key === 'Home') {
            event.preventDefault();
            show(0);
        } else if (key === 'End') {
            event.preventDefault();
            show(total - 1);
        } else if (key === 'Escape') {
            if (fullscreenElement()) {
                return;
            }
            event.preventDefault();
            exitPresentation();
        } else if (key === 'f' || key === 'F') {
            event.preventDefault();
            toggleFullscreen();
        } else if (key === 'p' || key === 'P') {
            event.preventDefault();
            toggleAutoplay();
        } else if (key === 't' || key === 'T') {
            event.preventDefault();
            toggleTheme();
        } else {
            return;
        }
        wake();
    });

    stage.addEventListener('click', function (event) {
        if (isInteractive(event.target)) {
            return;
        }
        var selection = window.getSelection ? window.getSelection() : null;
        if (selection && String(selection).length > 0) {
            return;
        }
        var rect = stage.getBoundingClientRect();
        if (event.clientX - rect.left < rect.width * 0.3) {
            prev();
        } else {
            next();
        }
    });

    stage.addEventListener('touchstart', function (event) {
        if (event.touches.length !== 1) {
            touchStart = null;
            return;
        }
        touchStart = { x: event.touches[0].clientX, y: event.touches[0].clientY, t: Date.now() };
    }, { passive: true });

    stage.addEventListener('touchend', function (event) {
        if (!touchStart || !event.changedTouches.length) {
            return;
        }
        var dx = event.changedTouches[0].clientX - touchStart.x;
        var dy = event.changedTouches[0].clientY - touchStart.y;
        var elapsed = Date.now() - touchStart.t;
        touchStart = null;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.4 && elapsed < 800) {
            if (dx < 0) {
                next();
            } else {
                prev();
            }
        }
    }, { passive: true });

    dots.forEach(function (dot) {
        dot.addEventListener('click', function () {
            show(parseInt(dot.getAttribute('data-cine-dot') || '0', 10));
        });
    });

    if (autoplayBtn) {
        autoplayBtn.addEventListener('click', toggleAutoplay);
    }
    if (themeBtn) {
        themeBtn.addEventListener('click', toggleTheme);
    }
    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', toggleFullscreen);
    }
    if (restartBtn) {
        restartBtn.addEventListener('click', function () {
            show(0);
        });
    }

    document.addEventListener('fullscreenchange', syncFullscreen);
    document.addEventListener('webkitfullscreenchange', syncFullscreen);
    window.addEventListener('hashchange', function () {
        show(indexFromHash());
    });
    ['mousemove', 'mousedown', 'touchstart', 'focusin'].forEach(function (name) {
        document.addEventListener(name, wake, { passive: true });
    });
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            window.clearTimeout(autoplayTimer);
        } else {
            scheduleAutoplay();
        }
    });

    slides.forEach(function (slide) {
        slide.classList.remove('is-active');
    });
    show(indexFromHash(), { instant: true });
    body.classList.add('is-ready');
    wake();
})();
