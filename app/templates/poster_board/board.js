(function(){
  "use strict";

  /* EventTracker poster board. The payload is built by
     app/services/poster_board.py and embedded as JSON, so the in-app board
     and the self-contained HTML export run the exact same script.
     Sanitized rich text is rendered server-side into inert templates and cloned
     into detail sheets, never reparsed from the JSON payload.
     body[data-entry-url] is only present in the live app; without it the
     board renders no links back into EventTracker (the export case). */
  var DATA = JSON.parse(document.getElementById("board-data").textContent);
  var items = DATA.items || [];
  items.forEach(function(it){
    it.hay = [it.headline, it.dek, it.category, it.group, it.date]
      .concat(it.tags || []).join(" ").toLowerCase();
  });
  var entryUrl = document.body.getAttribute("data-entry-url") || "";
  var board = document.getElementById("board");
  var emptyMsg = document.getElementById("empty");
  var pop = document.getElementById("pop");
  var slot = document.getElementById("pop-slot");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* poster footprint in grid cells, by impact tier. This is the ONLY
     thing that encodes importance — placement is deliberately random. */
  var FOOT = {1:{w:3,h:3}, 2:{w:2,h:2}, 3:{w:2,h:1}, 4:{w:1,h:1}};

  /* ---------- sticker pack ----------
     Board furniture for the cells no poster claimed. Swap this array to change
     the board's personality. Three kinds of sticker:
       {emoji:0x1F440}             a system emoji by codepoint - zero bytes, offline
       {stamp:"TEXT", tint:"#hex"} a rubber-stamped phrase
       {note:"text"}               a scribbled sticky note
     Nothing here is clickable, none of it is announced to screen readers, and
     it all disappears in the narrow-screen fallback. */
  var STICKERS = [
    {emoji:0x1F440},                              /* eyes */
    {stamp:"KEY MOMENT", tint:"#C4342B"},
    {note:"remember this?"},
    {emoji:0x1F4CC},                              /* pushpin */
    {stamp:"CITATION NEEDED", tint:"#2F5FA8"},
    {emoji:0x1F5D3},                              /* spiral calendar */
    {note:"connect the dots"},
    {emoji:0x1F4C8},                              /* chart increasing */
    {stamp:"WORTH A LOOK", tint:"#6B3FA0"},
    {emoji:0x1F9ED},                              /* compass */
    {note:"what changed?"},
    {emoji:0x26A1},                               /* high voltage */
    {stamp:"FOLLOW UP", tint:"#8A5A0B"},
    {emoji:0x1F9E0},                              /* brain */
    {note:"bigger than it looks"},
    {emoji:0x1F50D},                              /* magnifying glass */
    {stamp:"ON THE RECORD", tint:"#4F7211"},
    {emoji:0x1F4DA}                               /* books */
  ];

  var state = {cat:"All", q:""};
  var shown = [];              // items on the board right now (pack order)
  var nodes = {};              // entry id -> element, reused across re-packs
  var openIdx = -1;
  var lastFocus = null;
  var first = true;

  /* ---------- helpers ---------- */
  function esc(s){
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  }
  function safeUrl(u){
    try {
      var p = new URL(u);
      return p.protocol === "http:" || p.protocol === "https:" ? p.href : "";
    } catch(e){ return ""; }
  }
  function pad2(n){ return (n < 10 ? "0" : "") + n; }
  function clamp(v, lo, hi){ return v < lo ? lo : (v > hi ? hi : v); }
  /* seeded PRNG so a given board always packs the same way */
  function rng(seed){
    return function(){
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  /* stable tilt per poster; bigger posters lean less */
  function tilt(it){
    var h = Math.sin(it.rank * 12.9898) * 43758.5453;
    var amp = it.tier === 1 ? .8 : it.tier === 2 ? 1.2 : it.tier === 3 ? 1.6 : 2;
    return ((((h - Math.floor(h)) * 2) - 1) * amp).toFixed(2);
  }

  /* sort_key is YYYYMMDD with 00 for an unknown day */
  function splitKey(k){
    return [Math.floor(k / 10000), Math.floor(k / 100) % 100, k % 100];
  }
  function keyDate(k){
    var p = splitKey(k);
    return Date.UTC(p[0], Math.max(p[1], 1) - 1, Math.max(p[2], 1));
  }
  /* posters show a short date; the year only appears when the board spans years */
  var MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  var years = {};
  items.forEach(function(it){ years[Math.floor((it.sort_key || 0) / 10000)] = 1; });
  var multiYear = Object.keys(years).length > 1;
  function shortDate(it){
    var p = splitKey(it.sort_key || 0), y = p[0], m = p[1], d = p[2];
    if(m < 1 || m > 12) return esc(it.date);
    var out = MONTHS[m - 1] + (d ? " " + d : "");
    return multiYear || !d ? out + " " + y : out;
  }
  var newest = items.reduce(function(a, it){ return Math.max(a, it.sort_key || 0); }, 0);
  function isNew(it){
    if(!newest || !it.sort_key || items.length < 2) return false;
    var d = (keyDate(newest) - keyDate(it.sort_key)) / 86400000;
    return d >= 0 && d <= 3;
  }

  /* ---------- the packer ----------
     Fit every poster into a cols x rows lattice sized to the board box.
     Big posters are placed first, each into a RANDOM free slot, so the
     wall has no reading order. Retry with new seeds, then more rows,
     until everything fits. */
  function packOnce(list, cols, rows, seed){
    var occ = new Uint8Array(cols * rows);
    var rand = rng(seed);
    var out = [];
    for(var i = 0; i < list.length; i++){
      var f = FOOT[list[i].tier];
      var spots = [];
      for(var y = 0; y + f.h <= rows; y++){
        for(var x = 0; x + f.w <= cols; x++){
          var ok = true;
          for(var dy = 0; dy < f.h && ok; dy++){
            for(var dx = 0; dx < f.w; dx++){
              if(occ[(y + dy) * cols + x + dx]){ ok = false; break; }
            }
          }
          if(ok) spots.push([x, y]);
        }
      }
      if(!spots.length) return null;
      var s = spots[Math.floor(rand() * spots.length)];
      for(var oy = 0; oy < f.h; oy++){
        for(var ox = 0; ox < f.w; ox++) occ[(s[1] + oy) * cols + s[0] + ox] = 1;
      }
      out.push({it:list[i], x:s[0], y:s[1], w:f.w, h:f.h});
    }
    return {cells:out, occ:occ};
  }

  /* The cells no poster claimed, grouped into the largest rectangles that fit.
     These are the sticker slots. */
  function freeBlocks(occ, cols, rows){
    var used = new Uint8Array(cols * rows);
    var out = [];
    function isFree(x, y, w, h){
      if(x + w > cols || y + h > rows) return false;
      for(var dy = 0; dy < h; dy++){
        for(var dx = 0; dx < w; dx++){
          var k = (y + dy) * cols + x + dx;
          if(occ[k] || used[k]) return false;
        }
      }
      return true;
    }
    var shapes = [[2,2],[2,1],[1,2],[1,1]];
    for(var y = 0; y < rows; y++){
      for(var x = 0; x < cols; x++){
        for(var s = 0; s < shapes.length; s++){
          var w = shapes[s][0], h = shapes[s][1];
          if(!isFree(x, y, w, h)) continue;
          for(var dy = 0; dy < h; dy++){
            for(var dx = 0; dx < w; dx++) used[(y + dy) * cols + x + dx] = 1;
          }
          out.push({x:x, y:y, w:w, h:h});
          break;
        }
      }
    }
    return out;
  }

  function pack(list, boxW, boxH){
    /* order big-to-small for packing efficiency; visual order stays random */
    var byArea = list.slice().sort(function(a, b){
      var fa = FOOT[a.tier], fb = FOOT[b.tier];
      return (fb.w * fb.h) - (fa.w * fa.h);
    });
    var area = byArea.reduce(function(n, it){
      var f = FOOT[it.tier]; return n + f.w * f.h;
    }, 0);
    if(!area) return {cells:[], cols:1, rows:1, free:[]};

    /* choose a lattice whose cells come out roughly square */
    var ar = boxW / Math.max(boxH, 1);
    /* less slack when few posters remain, or the wall reads as mostly cork */
    var slack = list.length <= 8 ? 1.0 : 1.1;
    var cols = clamp(Math.round(Math.sqrt(area * slack * ar)), 4, 26);
    var widest = byArea.reduce(function(n, it){ return Math.max(n, FOOT[it.tier].w); }, 1);
    cols = Math.max(cols, widest);
    var rows = Math.max(Math.ceil(area * slack / cols), 2);

    for(var attempt = 0; attempt < 40; attempt++){
      var got = packOnce(byArea, cols, rows, 97 + attempt * 7919);
      if(got) return {cells:got.cells, cols:cols, rows:rows,
                      free:freeBlocks(got.occ, cols, rows)};
      if(attempt % 5 === 4) rows++;          // loosen the lattice and try again
    }
    var last = packOnce(byArea, cols, rows + 4, 13);
    return {cells:last ? last.cells : [], cols:cols, rows:rows + 4,
            free:last ? freeBlocks(last.occ, cols, rows + 4) : []};
  }

  /* ---------- poster markup + adaptive type ---------- */
  function build(it){
    var el = document.createElement("div");
    el.className = "poster";
    el.dataset.hue = it.hue;
    el.dataset.key = it.id;
    var srcs = it.sources || [];
    var s0 = srcs[0];
    el.innerHTML =
      '<span class="tape l"></span><span class="tape r"></span>' +
      '<article class="paper" style="--rot:' + tilt(it) + 'deg" tabindex="0" role="button"' +
        ' aria-label="Open story: ' + esc(it.headline) + '">' +
        '<span class="pin"></span>' +
        '<div class="row"><span class="cat">' + esc(it.category) + '</span>' +
          (isNew(it) ? '<span class="flag">New</span>' : '') + '</div>' +
        '<h3 class="headline">' + esc(it.headline) + '</h3>' +
        '<p class="dek">' + esc(it.dek) + '</p>' +
        '<div class="foot"><span>' + shortDate(it) + '</span>' +
          '<span class="src">' + esc(s0 ? s0.name : it.group) + '</span></div>' +
      '</article>';
    el.parts = {
      paper: el.querySelector(".paper"),
      headline: el.querySelector(".headline"),
      dek: el.querySelector(".dek"),
      foot: el.querySelector(".foot"),
      pin: el.querySelector(".pin"),
      flag: el.querySelector(".flag"),
      tapes: el.querySelectorAll(".tape")
    };
    return el;
  }

  var FITVARS = ["--pad","--fs","--lh","--dfs","--tagfs","--footfs","--tagls","--hlgap","--pinpad","--hl","--dl"];

  /* An off-screen ruler measures how many lines a headline really wraps to.
     Guessing from character counts is unreliable — one long word ("PARLIAMENT'S")
     can blow out a line — and the posters themselves cannot be measured while
     their width is mid-transition. */
  var css = getComputedStyle(document.documentElement);
  var FACE_DISPLAY = css.getPropertyValue("--display").trim();
  var FACE_SANS = css.getPropertyValue("--sans").trim();
  var ruler = document.createElement("div");
  ruler.setAttribute("aria-hidden", "true");
  ruler.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;pointer-events:none";
  document.body.appendChild(ruler);

  /* Measuring forces a synchronous layout, so remember every answer: re-filtering
     or resizing back to a tile size the board has seen costs nothing. */
  var lineCache = {};
  var lineCacheSize = 0;
  function lineCount(text, w, fs, lh, big){
    var key = (big ? "B" : "s") + w.toFixed(1) + "|" + fs.toFixed(2) + "|" + text;
    if(key in lineCache) return lineCache[key];
    if(++lineCacheSize > 4000){ lineCache = {}; lineCacheSize = 1; }
    return (lineCache[key] = measureLines(text, w, fs, lh, big));
  }
  function measureLines(text, w, fs, lh, big){
    ruler.style.width = w + "px";
    ruler.style.font = (big ? "900 " : "800 ") + fs + "px/" + lh + " " + (big ? FACE_DISPLAY : FACE_SANS);
    ruler.style.textTransform = big ? "uppercase" : "none";
    ruler.style.letterSpacing = big ? "-.022em" : "-.012em";
    ruler.textContent = text;
    return Math.max(1, Math.round(ruler.scrollHeight / (fs * lh)));
  }

  /* size the type to the tile it actually got, so text always fits its poster
     and nothing ever needs to scroll. w == null means flow fallback. */
  function setShown(parts, dek, foot, pin, flag, tapes){
    parts.dek.style.display = dek ? "" : "none";
    parts.foot.style.display = foot ? "" : "none";
    parts.pin.style.display = pin ? "" : "none";
    if(parts.flag) parts.flag.style.display = flag ? "" : "none";
    parts.tapes[0].style.display = tapes ? "" : "none";
    parts.tapes[1].style.display = tapes ? "" : "none";
  }

  function fit(el, w, h){
    var parts = el.parts;
    var paper = parts.paper;

    if(w == null){
      FITVARS.forEach(function(p){ paper.style.removeProperty(p); });
      paper.classList.remove("big");
      setShown(parts, true, true, true, true, false);
      return;
    }

    var big = Math.min(w, h) >= 190 && w * h >= 62000;
    var pad = w < 120 ? 7 : (w < 150 ? 9 : (w < 250 ? 12 : (w < 380 ? 16 : 20)));
    var innerW = w - pad * 2;
    var text = parts.headline.textContent || "";
    var showFoot = h >= 96 && w >= 140;
    var showFlag = w >= 190;
    /* on a cramped poster the pushpin would eat the category tag, so drop it */
    var showPin = !big && w >= 160;

    /* The headline owns the poster: start from a size proportional to the tile,
       measure how many lines it really needs, and shrink until the whole thing
       fits. The dek only gets what the headline leaves behind. */
    /* Ceilings scale with the tile: a poster left nearly alone on the board
       (a narrow filter) should grow into real poster type, not sit half-empty
       with 16px furniture. */
    var capTag = clamp(w * 0.028, 11, 20);
    var capDek = clamp(w * 0.026, 16, 30);
    var capGap = clamp(w * 0.035, 13, 30);
    var capFoot = clamp(w * 0.026, 9.5, 16);

    var fs = clamp(Math.sqrt(w * h) * 0.118, 11.5, 96);
    var lh = big ? 1.05 : 1.14;
    var tagfs, hlgap, dfs, footfs, avail, maxLines, lines;
    for(var guard = 0; guard < 10; guard++){
      tagfs = clamp(fs * 0.46, 8, capTag);
      hlgap = clamp(fs * 0.40, 5, capGap);
      dfs = clamp(fs * 0.44, 11.5, capDek);
      footfs = clamp(fs * 0.26, 9, capFoot);
      avail = h - pad * 2 - (tagfs + 9) - hlgap - (showFoot ? footfs + 14 : 0);
      maxLines = Math.max(1, Math.floor(avail / (fs * lh)));
      lines = lineCount(text, innerW, fs, lh, big);
      if(lines <= maxLines || fs <= 11.6) break;
      fs = Math.max(11.5, fs * clamp(Math.sqrt(maxLines / lines), 0.82, 0.94));
    }
    var hlLines = Math.min(lines, maxLines);

    /* the dek gets whatever vertical room the headline did not use */
    var leftover = avail - hlLines * fs * lh - 7;
    var dlLines = Math.floor(leftover / (dfs * 1.42));
    var showDek = dlLines >= 2 && w >= 200;
    if(!showDek) dlLines = 0;

    paper.classList.toggle("big", big);
    paper.style.setProperty("--pad", pad + "px");
    paper.style.setProperty("--fs", fs.toFixed(1) + "px");
    paper.style.setProperty("--lh", lh);
    paper.style.setProperty("--dfs", dfs.toFixed(1) + "px");
    paper.style.setProperty("--tagfs", tagfs.toFixed(1) + "px");
    paper.style.setProperty("--footfs", footfs.toFixed(1) + "px");
    paper.style.setProperty("--tagls", tagfs < 9.5 ? ".06em" : ".12em");
    paper.style.setProperty("--hlgap", hlgap.toFixed(0) + "px");
    paper.style.setProperty("--pinpad", showPin ? "20px" : "0px");
    paper.style.setProperty("--hl", hlLines);
    paper.style.setProperty("--dl", dlLines);
    /* tape corners on the big sheets, a pushpin on the middling ones */
    setShown(parts, showDek, showFoot, showPin, showFlag, big);
  }

  /* ---------- stickers ---------- */
  function buildSticker(spec, i){
    var el = document.createElement("div");
    el.className = "sticker";
    el.setAttribute("aria-hidden", "true");
    var h = Math.sin((i + 3) * 78.233) * 43758.5453;
    el.style.setProperty("--rot", ((((h - Math.floor(h)) * 2) - 1) * 7).toFixed(2) + "deg");
    el.style.setProperty("--delay", ((i % 6) * 0.7).toFixed(1) + "s");
    if(spec.emoji){
      el.innerHTML = '<span class="s-emoji">' + String.fromCodePoint(spec.emoji) + '</span>';
    } else if(spec.stamp){
      el.innerHTML = '<span class="s-stamp" style="--tint:' + esc(spec.tint || "#C4342B") +
        '">' + esc(spec.stamp) + '</span>';
    } else if(spec.note){
      el.innerHTML = '<span class="s-note">' + esc(spec.note) + '</span>';
    }
    return el;
  }

  /* Rotate through the KINDS of sticker rather than striding the flat array:
     with only a handful of gaps a stride can skip an entire kind. */
  var STICKER_KINDS = ["emoji", "stamp", "note"].map(function(k){
    return STICKERS.filter(function(s){ return s[k] !== undefined; });
  }).filter(function(g){ return g.length; });

  function dressGaps(free, cw, ch, gap){
    Array.prototype.forEach.call(board.querySelectorAll(".sticker"), function(s){
      s.parentNode.removeChild(s);
    });
    if(!STICKER_KINDS.length) return;
    var kinds = STICKER_KINDS;
    var frag = document.createDocumentFragment();
    free.forEach(function(f, i){
      var group = kinds[i % kinds.length];
      var spec = group[Math.floor(i / kinds.length) % group.length];
      var el = buildSticker(spec, i);
      var w = f.w * cw + (f.w - 1) * gap;
      var hh = f.h * ch + (f.h - 1) * gap;
      el.style.width = w + "px";
      el.style.height = hh + "px";
      el.style.transform = "translate3d(" + (f.x * (cw + gap)) + "px," +
        (f.y * (ch + gap)) + "px,0)";
      el.style.setProperty("--efs", (Math.min(w, hh) * 0.58).toFixed(0) + "px");
      /* a long unbreakable word ("UNCONFIRMED") has to shrink to fit its cell,
         so size the stamp off its longest word rather than the whole phrase */
      var longest = spec.stamp
        ? spec.stamp.split(/\s+/).reduce(function(n, s){ return Math.max(n, s.length); }, 1)
        : 1;
      el.style.setProperty("--tfs",
        clamp(Math.min(w * 0.13, (w * 0.80) / (longest * 0.72)), 9, 28).toFixed(1) + "px");
      el.style.setProperty("--nfs", clamp(w * 0.105, 10, 21).toFixed(1) + "px");
      frag.appendChild(el);
    });
    board.appendChild(frag);
  }

  /* ---------- filtering ---------- */
  function compute(){
    var q = state.q.trim().toLowerCase();
    return items.filter(function(it){
      if(state.cat !== "All" && it.category !== state.cat) return false;
      if(!q) return true;
      return it.hay.indexOf(q) > -1;
    });
  }

  /* ---------- lay the board out ---------- */
  var flowing = false;
  function layout(){
    var boxW = board.clientWidth;
    var boxH = board.clientHeight;
    var list = compute();
    shown = list;
    emptyMsg.hidden = list.length > 0;

    /* too cramped to pack a wall — fall back to a scrolling column */
    var wantFlow = window.innerWidth < 620 || window.innerHeight < 460;
    if(wantFlow !== flowing){
      flowing = wantFlow;
      document.body.classList.toggle("flowing", flowing);
      boxW = board.clientWidth; boxH = board.clientHeight;
    }

    var seen = {};
    if(flowing){
      list.forEach(function(it){
        var el = nodes[it.id] || (nodes[it.id] = build(it));
        seen[it.id] = 1;
        el.style.transform = "";
        el.style.width = el.style.height = "";
        fit(el, null, null);
        if(!el.parentNode) board.appendChild(el);
        el.classList.remove("out");
        el.classList.add("in");
      });
    } else {
      var plan = pack(list, boxW, boxH);
      var gap = clamp(Math.min(boxW / plan.cols, boxH / plan.rows) * 0.11, 7, 16);
      var cw = (boxW - (plan.cols - 1) * gap) / plan.cols;
      var ch = (boxH - (plan.rows - 1) * gap) / plan.rows;

      dressGaps(plan.free || [], cw, ch, gap);

      plan.cells.forEach(function(c, i){
        var it = c.it;
        var el = nodes[it.id] || (nodes[it.id] = build(it));
        seen[it.id] = 1;
        var w = c.w * cw + (c.w - 1) * gap;
        var h = c.h * ch + (c.h - 1) * gap;
        var x = c.x * (cw + gap);
        var y = c.y * (ch + gap);
        var fresh = !el.parentNode;
        el.style.width = w + "px";
        el.style.height = h + "px";
        el.style.transform = "translate3d(" + x + "px," + y + "px,0)";
        if(fresh) board.appendChild(el);
        fit(el, w, h);
        el.classList.remove("out");
        if(fresh && !reduced){
          var paper = el.parts.paper;
          paper.style.transitionDelay = (first ? Math.min(i, 26) * 24 : 0) + "ms";
          requestAnimationFrame(function(){
            requestAnimationFrame(function(){
              el.classList.add("in");
              setTimeout(function(){ paper.style.transitionDelay = ""; }, 900);
            });
          });
        } else {
          el.classList.add("in");
        }
      });
    }

    /* retire posters that fell out of the filter */
    Object.keys(nodes).forEach(function(k){
      if(seen[k]) return;
      var el = nodes[k];
      delete nodes[k];
      if(!el.parentNode) return;
      el.classList.remove("in");
      el.classList.add("out");
      setTimeout(function(){ if(el.parentNode) el.parentNode.removeChild(el); }, reduced ? 0 : 340);
    });

    first = false;
  }

  /* ---------- popup ---------- */
  /* body_html was sanitized server-side to a small allow-list of text tags
     (app/services/formatting.py), so it is safe to drop into the sheet. */
  function connectionHTML(c){
    var label = esc(c.title) + '<small>' + esc(c.date) +
      (c.note ? ' &middot; ' + esc(c.note) : '') + '</small>';
    if(shown.some(function(it){ return it.id === c.id; })){
      return '<button type="button" data-act="goto" data-id="' + Number(c.id) + '">' + label + '</button>';
    }
    if(entryUrl){
      return '<a href="' + esc(entryUrl.replace("{id}", Number(c.id))) + '">' + label + '</a>';
    }
    return '<span>' + label + '</span>';
  }

  function sheetHTML(it, pos, total){
    var srcs = (it.sources || []).filter(function(s){ return safeUrl(s.url); });
    var conns = it.connections || [];
    var n = Number(it.connection_count || 0);
    return '<div class="sheet" data-hue="' + Number(it.hue) + '">' +
      '<div class="sheet__band">' +
        '<span>' + esc(it.category) + '</span>' +
        '<span class="score">' + esc(it.date) + ' &middot; #' + Number(it.rank) + ' of ' + items.length + '</span>' +
        '<button class="x" data-act="close" aria-label="Close">&times;</button>' +
      '</div>' +
      '<div class="sheet__body">' +
        '<h2>' + esc(it.headline) + '</h2>' +
        '<div class="sheet__cols">' +
          '<div>' +
            '<h4>What happened</h4>' +
            '<div class="body"></div>' +
          '</div>' +
          '<div>' +
            '<dl class="facts">' +
              '<dt>When</dt><dd>' + esc(it.date) + '</dd>' +
              '<dt>Group</dt><dd>' + esc(it.group) + '</dd>' +
              '<dt>Linked</dt><dd>' + n + ' connected event' + (n === 1 ? '' : 's') + '</dd>' +
            '</dl>' +
            (conns.length
              ? '<h4>Connected events</h4><div class="links">' + conns.map(connectionHTML).join("") + '</div>'
              : '') +
            (srcs.length
              ? '<h4>Sources</h4><div class="links">' +
                srcs.map(function(s){
                  return '<a href="' + esc(safeUrl(s.url)) + '" target="_blank" rel="noopener noreferrer">' +
                    esc(s.name) + ' &nearr;</a>';
                }).join("") + '</div>'
              : '') +
            (entryUrl
              ? '<h4>In EventTracker</h4><div class="links"><a href="' +
                esc(entryUrl.replace("{id}", Number(it.id))) + '">Open full entry &rarr;</a></div>'
              : '') +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="sheet__nav">' +
        (it.tags && it.tags.length
          ? '<div class="tags">' +
            it.tags.map(function(t){ return '<span class="tag">' + esc(t) + '</span>'; }).join("") +
            '</div>'
          : '<span></span>') +
        '<button data-act="prev"' + (pos <= 1 ? " disabled" : "") + '>&larr; Prev</button>' +
        '<button data-act="next"' + (pos >= total ? " disabled" : "") + '>Next &rarr;</button>' +
        '<span class="of">' + pad2(pos) + ' / ' + pad2(total) + '</span>' +
      '</div>' +
      '</div>';
  }

  function show(idx, dir){
    if(idx < 0 || idx >= shown.length) return;
    openIdx = idx;
    var it = shown[idx];
    // Generated by GitHub Copilot - Oct-02-2026
    var bodyTemplate = document.getElementById("board-body-" + Number(it.id));
    if(!bodyTemplate) throw new Error("Missing poster body template for entry " + Number(it.id));
    slot.innerHTML = sheetHTML(it, idx + 1, shown.length);
    slot.querySelector(".body").appendChild(bodyTemplate.content.cloneNode(true));
    if(!pop.open){
      pop.showModal();
    } else if(dir && !reduced){
      slot.animate([{opacity:0, transform:"translateX(" + (dir * 30) + "px)"}, {opacity:1, transform:"none"}],
        {duration:240, easing:"ease-out"});
    }
    var x = slot.querySelector('[data-act="close"]');
    if(x) x.focus();
    try { history.replaceState(null, "", "#e" + it.id); } catch(e){}
  }

  function closePop(){
    if(!pop.open) return;
    if(reduced){ pop.close(); return; }
    pop.classList.add("closing");
    setTimeout(function(){ pop.classList.remove("closing"); pop.close(); }, 175);
  }

  pop.addEventListener("close", function(){
    openIdx = -1;
    try { history.replaceState(null, "", location.pathname + location.search); } catch(e){}
    if(lastFocus && document.contains(lastFocus)) lastFocus.focus();
  });
  pop.addEventListener("cancel", function(e){ e.preventDefault(); closePop(); });
  pop.addEventListener("click", function(e){
    var act = e.target.closest("[data-act]");
    if(act){
      if(act.dataset.act === "close") closePop();
      if(act.dataset.act === "prev") show(openIdx - 1, -1);
      if(act.dataset.act === "next") show(openIdx + 1, 1);
      if(act.dataset.act === "goto"){
        var target = Number(act.dataset.id);
        var gi = shown.findIndex(function(it){ return it.id === target; });
        show(gi, gi > openIdx ? 1 : -1);
      }
      return;
    }
    if(!e.target.closest(".sheet")) closePop();
  });
  document.addEventListener("keydown", function(e){
    if(!pop.open) return;
    if(e.key === "ArrowLeft"){ e.preventDefault(); show(openIdx - 1, -1); }
    if(e.key === "ArrowRight"){ e.preventDefault(); show(openIdx + 1, 1); }
  });

  board.addEventListener("click", function(e){
    var paper = e.target.closest(".paper");
    if(!paper) return;
    var key = Number(paper.parentNode.dataset.key);
    var idx = shown.findIndex(function(it){ return it.id === key; });
    if(idx < 0) return;
    lastFocus = paper;
    show(idx, 0);
  });
  board.addEventListener("keydown", function(e){
    if(e.key !== "Enter" && e.key !== " ") return;
    var paper = e.target.closest(".paper");
    if(!paper) return;
    e.preventDefault();
    paper.click();
  });

  /* ---------- controls ---------- */
  var cats = [{name:"All", count:items.length, hue:null}].concat(DATA.categories || []);
  var chips = document.getElementById("chips");
  chips.innerHTML = items.length ? cats.map(function(c){
    var all = c.hue === null;
    return '<button class="chip" type="button" data-cat="' + esc(c.name) + '"' +
      (all ? '' : ' data-hue="' + Number(c.hue) + '"') + ' aria-pressed="' + all + '">' +
      (all ? "" : '<span class="dot"></span>') + esc(c.name) +
      '<span class="n">' + Number(c.count) + '</span></button>';
  }).join("") : "";
  chips.addEventListener("click", function(e){
    var chip = e.target.closest(".chip");
    if(!chip) return;
    state.cat = chip.dataset.cat;
    Array.prototype.forEach.call(chips.querySelectorAll(".chip"), function(c){
      c.setAttribute("aria-pressed", String(c === chip));
    });
    layout();
  });

  var timer;
  document.getElementById("search").addEventListener("input", function(e){
    var v = e.target.value;
    clearTimeout(timer);
    timer = setTimeout(function(){ state.q = v; layout(); }, 140);
  });

  var rt;
  window.addEventListener("resize", function(){
    clearTimeout(rt);
    rt = setTimeout(layout, 140);
  });

  /* ---------- go ---------- */
  layout();

  var countEl = document.getElementById("count");
  if(reduced || !items.length){
    countEl.textContent = items.length;
  } else {
    var t0 = null;
    requestAnimationFrame(function step(ts){
      if(t0 === null) t0 = ts;
      var p = Math.min((ts - t0) / 700, 1);
      countEl.textContent = Math.round(items.length * (1 - Math.pow(1 - p, 3)));
      if(p < 1) requestAnimationFrame(step);
    });
  }

  var m = /^#e(\d+)$/.exec(location.hash || "");
  if(m){
    var i = shown.findIndex(function(it){ return it.id === Number(m[1]); });
    if(i > -1) setTimeout(function(){ show(i, 0); }, 300);
  }
})();
