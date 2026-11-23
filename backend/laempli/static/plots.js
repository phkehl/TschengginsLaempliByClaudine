// Plots view (Jobs tab, sub-tab Plots): state changes of the jobs over time (GET /api/plots, history.py)
//
// Versions: timeline (one line per job, coloured by state), heatmap (one line per job, one cell per time step, the
// worst state in the step; faint: no change), activity (state changes per time step, stacked by state).
// Layout: tool row (zoom and pan buttons, shown time range), overview (all loaded time, the shown range as a
// window), time axis, scroll box with the lines, details panel (right on wide screens, else below). The canvas of
// the lines has the size of the scroll box: only the visible lines are drawn (a spacer gives the full height).
// Controls: wheel scrolls the lines, Ctrl + wheel (touchpad pinch) zooms, Shift + wheel (sideways swipe) pans, left
// drag pans, Shift + left drag zooms to a time range, middle drag pans, double click zooms in; keys + - left right
// 0 Esc. Click: details. The panel has a stack of views (job, event list, event) with a Back button.
// Selectors in the hash: v, range [s], kind, src, failed, q. Webhook texts go to the page as text (textContent).
// Uses globals of laempli.js: view, currentHash, timeOffset, webRoot, parseHash(), dhmsText(), eventsHash().

const PLOT_COLORS = { running: "#3b7dd8", waiting: "#5bc0de", success: "#2e9e5b", failed: "#d64545",
    warn: "#e0a020", canceled: "#8a9199", skipped: "#c3c8cd", unknown: "#a88bd0" };
const PLOT_SEVERITY = ["failed", "warn", "canceled", "running", "waiting", "success", "skipped", "unknown"];
const PLOT_STACK = ["success", "running", "waiting", "warn", "failed", "canceled", "skipped", "unknown"];
const PLOT_TICKS = [60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400, 172800, 604800];
const PLOT_STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
const PLOT_ROW = 16;  // [px] line height (timeline, heatmap)
const PLOT_AXIS = 22;  // [px] time axis height
const PLOT_OVERVIEW = 30;  // [px] overview height
const PLOT_BARS = 260;  // [px] activity plot height
const PLOT_BINS = 300;  // overview activity bins
const PLOT_MIN_SPAN = 60;  // [s] shortest shown range
const PLOT_RELOAD = 10000;  // [ms] min time between live reloads
const PLOT_LIST = 300;  // most events in a list
const PLOT_MOVE = 4;  // [px] mouse move: drag, not click
const PLOT_EDGE = 6;  // [px] overview window edge (zoom)

function startPlots(source) {
    const current = view;
    const apiRoot = (document.body.dataset.root + "/api");
    const el = (id) => document.getElementById(id);
    const box = el("plot_box");
    const scroller = el("plot_scroll");
    const spacer = el("plot_spacer");
    const canvas = el("plot_canvas");
    const axisCanvas = el("plot_axis");
    const overCanvas = el("plot_overview");
    const tip = el("plot_tip");
    const info = el("plot_info");
    const shownText = el("plot_shown");
    const panel = el("plot_panel");
    const search = el("plot_search");
    const keepRange = (Number(source.dataset.keepDays) * 86400);
    const sources = Array.from(document.querySelectorAll("#plot_sources [data-src]"), (btn) => btn.dataset.src)
        .filter((src) => src);
    let filter = { v: "heatmap", range: 86400, kind: "", src: [], failed: false, q: "" };
    let data = null;  // answer of /api/plots, with labels, rows by path, overview bins
    let zoom = null;  // [t0, t1] shown, null: all
    let geo = null;  // last layout: x0, x1, label width, rows, view height, ...
    let hover = null;  // { row, ix (point), cell: { ta, tb }, step } under the mouse
    let hoverTime = null;  // time under the mouse (cursor line)
    let selected = null;  // { key (path), ix, cell, step }: shown in the panel
    let drag = null;  // { mode: "pan", "band", "over-pan", "over-left", "over-right", x, y, moved, start: [t0, t1] }
    let stack = [];  // panel views
    let loadCtrl = null;
    let loadTimer = null;
    let searchTimer = null;
    let lastLoad = 0;
    let frame = 0;

    // ----- Selectors and hash -----

    function applyParams(params) {
        const versions = ["timeline", "heatmap", "activity"];
        const range = Number(params.get("range"));
        filter = {
            v: (versions.includes(params.get("v")) ? params.get("v") : "heatmap"),
            range: (((range >= 60) && (range <= keepRange)) ? range : 86400),
            kind: (["pipelines", "jobs"].includes(params.get("kind")) ? params.get("kind") : ""),
            src: (params.get("src") || "").split(",").filter((src) => sources.includes(src)),
            failed: (params.get("failed") === "1"),
            q: (params.get("q") || ""),
        };
        search.value = filter.q;
        mark();
    }
    function mark() {
        const set = (selector, key, active) => document.querySelectorAll(selector).forEach((btn) => {
            const on = active(btn.dataset[key]);
            btn.classList.toggle("active", on);
            btn.setAttribute("aria-pressed", String(on));
        });
        set("#plot_versions [data-v]", "v", (v) => (v === filter.v));
        set("#plot_ranges [data-range]", "range", (range) => (Number(range) === filter.range));
        set("#plot_kinds [data-kind]", "kind", (kind) => (kind === filter.kind));
        set("#plot_sources [data-src]", "src", (src) => (src ? filter.src.includes(src) : (filter.src.length === 0)));
        el("plot_failed").classList.toggle("active", filter.failed);
        el("plot_failed").setAttribute("aria-pressed", String(filter.failed));
    }
    function hashOf(next) {
        const params = new URLSearchParams();
        if (next.v !== "heatmap") {
            params.set("v", next.v);
        }
        if (next.range !== 86400) {
            params.set("range", String(next.range));
        }
        if (next.kind) {
            params.set("kind", next.kind);
        }
        if (next.src.length) {
            params.set("src", next.src.join(","));
        }
        if (next.failed) {
            params.set("failed", "1");
        }
        if (next.q) {
            params.set("q", next.q);
        }
        const query = params.toString();
        return ("#" + view.route + (query ? ("?" + query) : ""));
    }
    function writeParams() {
        history.replaceState(null, "", hashOf(filter));
        currentHash = location.hash;
        mark();
    }
    function onButton(selector, change) {
        document.querySelectorAll(selector).forEach((btn) => btn.addEventListener("click", () => {
            change(btn);
            writeParams();
        }));
    }
    onButton("#plot_versions [data-v]", (btn) => {
        filter.v = btn.dataset.v;
        hover = null;
        selected = null;
        scroller.scrollTop = 0;
        draw();
    });
    onButton("#plot_ranges [data-range]", (btn) => {
        filter.range = Number(btn.dataset.range);
        zoom = null;
        load();
    });
    onButton("#plot_kinds [data-kind]", (btn) => {
        filter.kind = btn.dataset.kind;
        load();
    });
    onButton("#plot_sources [data-src]", (btn) => {
        const src = btn.dataset.src;
        filter.src = ((!src) ? [] : (filter.src.includes(src) ? filter.src.filter((item) => (item !== src)) :
            filter.src.concat([src])));
        if (filter.src.length === sources.length) {
            filter.src = [];
        }
        load();
    });
    onButton("#plot_failed", () => {
        filter.failed = !filter.failed;
        load();
    });
    search.addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            filter.q = search.value.trim();
            writeParams();
            load();
        }, 400);
    });
    // Drill down: new filter with a history entry (the browser Back button goes back)
    function drillTo(changes) {
        location.hash = hashOf(Object.assign({}, filter, changes));
    }

    // ----- Data -----

    async function load() {
        clearTimeout(loadTimer);
        loadTimer = null;
        if (loadCtrl) {
            loadCtrl.abort();
        }
        const ctrl = new AbortController();
        loadCtrl = ctrl;
        const now = ((Date.now() / 1000) + timeOffset);
        const query = new URLSearchParams({ since: (now - filter.range).toFixed(3), until: (now + 60).toFixed(3) });
        if (filter.kind) {
            query.set("kind", filter.kind);
        }
        if (filter.src.length) {
            query.set("src", filter.src.join(","));
        }
        if (filter.failed) {
            query.set("failed", "1");
        }
        if (filter.q) {
            query.set("q", filter.q);
        }
        info.textContent = "Loading…";
        try {
            const res = await fetch((apiRoot + "/plots?" + query), { cache: "no-store", signal: ctrl.signal });
            if (!res.ok) {
                throw new Error("HTTP " + res.status);
            }
            const next = await res.json();
            if (current !== view) {
                return;
            }
            data = prepare(next);
            lastLoad = Date.now();
            info.textContent = (data.series.length + " jobs, " + data.points + " state changes" +
                (data.truncated ? " (too many, not all shown)" : ""));
            if (zoom) {
                setShown(zoom[0], zoom[1], false);
            }
            draw();
        } catch (err) {
            if (err.name !== "AbortError") {
                console.log("Plot data failed:", err);
                info.textContent = "No data: load failed";
            }
        }
    }
    // Labels (path without the source; below its pipeline: "└ <rest>"), row of each path, overview bins
    function prepare(next) {
        let parent = null;
        next.rows = new Map();
        next.series.forEach((item, row) => {
            const sub = (parent && (item.path.length > parent.length) &&
                parent.every((name, ix) => (name === item.path[ix])));
            item.sub = !!sub;
            item.label = (sub ? ("└ " + item.path.slice(parent.length).join(" › ")) : item.path.slice(1).join(" › "));
            item.key = JSON.stringify(item.path);
            next.rows.set(item.key, row);
            if (!sub) {
                parent = item.path;
            }
        });
        next.t0 = next.since;
        next.t1 = next.now;
        next.bins = new Array(PLOT_BINS).fill(0);
        const size = ((next.t1 - next.t0) / PLOT_BINS);
        next.series.forEach((item) => item.points.forEach((point) => {
            const bin = Math.floor((point[0] - next.t0) / size);
            if ((bin >= 0) && (bin < PLOT_BINS)) {
                next.bins[bin] += 1;
            }
        }));
        return next;
    }
    const shown = () => (zoom || [data.t0, data.t1]);
    const stateOf = (point) => data.states[point[1]];
    // End of the state of point ix: next point, else now
    const endOf = (item, ix) => (((ix + 1) < item.points.length) ? item.points[ix + 1][0] : data.now);
    // Index of the last point at or before a time, -1 if none
    function pointAt(item, stamp) {
        let lo = 0;
        let hi = (item.points.length - 1);
        let found = -1;
        while (lo <= hi) {
            const mid = ((lo + hi) >> 1);
            if (item.points[mid][0] <= stamp) {
                found = mid;
                lo = (mid + 1);
            } else {
                hi = (mid - 1);
            }
        }
        return found;
    }

    // ----- Zoom and pan -----

    // Show a time range (at least PLOT_MIN_SPAN, inside the data); all: no zoom
    function setShown(t0, t1, redraw = true) {
        const full = (data.t1 - data.t0);
        let span = Math.min(full, Math.max(PLOT_MIN_SPAN, Math.abs(t1 - t0)));
        let lo = Math.min(t0, t1);
        if ((Math.abs(t1 - t0)) < PLOT_MIN_SPAN) {
            lo = (((t0 + t1) / 2) - (span / 2));
        }
        lo = Math.max(data.t0, Math.min(lo, data.t1 - span));
        span = Math.min(span, data.t1 - lo);
        zoom = ((span >= (full - 0.5)) ? null : [lo, lo + span]);
        if (redraw) {
            draw();
        }
    }
    function zoomAt(stamp, factor) {
        const [w0, w1] = shown();
        setShown(stamp - ((stamp - w0) * factor), stamp + ((w1 - stamp) * factor));
    }
    function panBy(seconds) {
        const [w0, w1] = shown();
        setShown(w0 + seconds, w1 + seconds);
    }
    function zoomCentre(factor) {
        const [w0, w1] = shown();
        zoomAt(((w0 + w1) / 2), factor);
    }
    const pxTime = (dx) => {
        const [w0, w1] = shown();
        return ((dx * (w1 - w0)) / (geo.x1 - geo.x0));
    };
    const timeAt = (x) => {
        const [w0, w1] = shown();
        return (w0 + (((x - geo.x0) / (geo.x1 - geo.x0)) * (w1 - w0)));
    };
    const xOf = (stamp) => {
        const [w0, w1] = shown();
        return (geo.x0 + (((stamp - w0) / (w1 - w0)) * (geo.x1 - geo.x0)));
    };
    document.querySelectorAll("#plot_zoom [data-act]").forEach((btn) => btn.addEventListener("click", () => {
        if (!data) {
            return;
        }
        const span = (shown()[1] - shown()[0]);
        ({ in: () => zoomCentre(0.5), out: () => zoomCentre(2), left: () => panBy(-0.2 * span),
            right: () => panBy(0.2 * span), all: () => setShown(data.t0, data.t1) })[btn.dataset.act]();
    }));

    // ----- Drawing helpers -----

    function themeColors() {
        const css = getComputedStyle(document.body);
        const get = (name) => css.getPropertyValue(name).trim();
        return { text: get("--bs-body-color"), muted: get("--bs-secondary-color"), grid: get("--bs-border-color"),
            zebra: get("--bs-tertiary-bg"), accent: get("--bs-primary-rgb"), strong: get("--bs-emphasis-color"),
            bg: get("--bs-body-bg") };
    }
    function setup(target, width, height) {
        const ratio = (window.devicePixelRatio || 1);
        if ((target.width !== Math.round(width * ratio)) || (target.height !== Math.round(height * ratio))) {
            target.width = Math.round(width * ratio);
            target.height = Math.round(height * ratio);
            target.style.width = (width + "px");
            target.style.height = (height + "px");
        }
        const ctx = target.getContext("2d");
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, height);
        ctx.font = ("12px " + getComputedStyle(document.body).fontFamily);
        ctx.textBaseline = "middle";
        return ctx;
    }
    function fit(ctx, text, width) {
        if (ctx.measureText(text).width <= width) {
            return text;
        }
        let lo = 0;
        let hi = text.length;
        while (lo < hi) {
            const mid = Math.ceil((lo + hi) / 2);
            if (ctx.measureText(text.slice(0, mid) + "…").width <= width) {
                lo = mid;
            } else {
                hi = (mid - 1);
            }
        }
        return (text.slice(0, lo) + "…");
    }
    const pad = (num) => String(num).padStart(2, "0");
    const dayText = (date) => (date.getDate() + "." + (date.getMonth() + 1) + ".");
    function timeText(stamp, withDate) {
        const date = new Date(stamp * 1000);
        return ((withDate ? (dayText(date) + " ") : "") + pad(date.getHours()) + ":" + pad(date.getMinutes()));
    }
    function longTime(stamp) {
        const date = new Date(stamp * 1000);
        return (date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) + " " +
            pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds()));
    }
    function ticks() {
        const [w0, w1] = shown();
        const step = (PLOT_TICKS.find((size) => (((geo.x1 - geo.x0) * size) / (w1 - w0)) >= 80) ||
            PLOT_TICKS[PLOT_TICKS.length - 1]);
        const tz = (new Date().getTimezoneOffset() * 60);
        const list = [];
        for (let stamp = ((Math.ceil((w0 - tz) / step) * step) + tz); stamp <= w1; stamp += step) {
            list.push({ stamp, step });
        }
        return list;
    }
    // Time step of the heatmap and the activity bars: a round size, cells of about px pixels
    function stepSize(px) {
        const [w0, w1] = shown();
        const want = (((w1 - w0) * px) / (geo.x1 - geo.x0));
        return (PLOT_STEPS.find((size) => (size >= want)) || 86400);
    }

    // ----- Layout -----

    function layout() {
        const width = Math.max(320, box.clientWidth);
        const lines = (filter.v !== "activity");
        const labelWidth = (lines ? Math.min(340, Math.round(width * 0.32)) : 0);
        const rows = (lines ? data.series.length : 0);
        const total = (lines ? Math.max(2 * PLOT_ROW, (rows * PLOT_ROW) + 4) : (PLOT_BARS + 6));
        spacer.style.height = (total + "px");
        scroller.classList.toggle("plot-noscroll", !lines);
        // Scroll box: down to the bottom of the window (min. 300 px), so the page does not scroll for the plot
        const free = Math.max(300, Math.floor(window.innerHeight - (scroller.getBoundingClientRect().top +
            window.scrollY) - 12));
        if (lines && (scroller.style.maxHeight !== (free + "px"))) {
            scroller.style.maxHeight = (free + "px");
        }
        const height = Math.max(PLOT_ROW, Math.min(total, scroller.clientHeight || total));
        const top = (lines ? scroller.scrollTop : 0);
        geo = { width, height, labelWidth, x0: (lines ? (labelWidth + 6) : 44), x1: (width - 8), lines, top, rows,
            first: Math.max(0, Math.floor(top / PLOT_ROW)),
            last: Math.min(rows - 1, Math.ceil((top + height) / PLOT_ROW)) };
    }
    const rowY = (row) => ((row * PLOT_ROW) - geo.top);

    // ----- Draw -----

    function draw() {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
            if ((!data) || (current !== view)) {
                return;
            }
            layout();
            const colors = themeColors();
            const ctx = setup(canvas, geo.width, geo.height);
            drawGrid(ctx, colors);
            if (geo.lines) {
                drawRows(ctx, colors);
                (filter.v === "timeline" ? drawTimeline : drawHeatmap)(ctx, colors);
            } else {
                drawActivity(ctx, colors);
            }
            drawCursor(ctx, colors);
            if (drag && (drag.mode === "band") && drag.moved) {
                const xa = Math.max(geo.x0, Math.min(drag.x, drag.x2));
                const xb = Math.min(geo.x1, Math.max(drag.x, drag.x2));
                ctx.fillStyle = ("rgba(" + colors.accent + ", 0.18)");
                ctx.fillRect(xa, 0, xb - xa, geo.height);
            }
            if ((!data.series.length)) {
                ctx.fillStyle = colors.muted;
                ctx.textAlign = "center";
                ctx.fillText("No state changes in this time range", geo.width / 2, Math.min(geo.height, 60) / 2);
                ctx.textAlign = "left";
            }
            drawAxis(colors);
            drawOverview(colors);
            const [w0, w1] = shown();
            const days = ((w1 - w0) >= 86400) || (new Date(w0 * 1000).getDate() !== new Date(w1 * 1000).getDate());
            shownText.textContent = (timeText(w0, days) + " – " + timeText(w1, days) + (zoom ? " (zoomed)" : ""));
        });
    }
    function drawGrid(ctx, colors) {
        ctx.strokeStyle = colors.grid;
        ctx.lineWidth = 1;
        ticks().forEach(({ stamp }) => {
            const x = (Math.round(xOf(stamp)) + 0.5);
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, geo.height);
            ctx.stroke();
        });
    }
    function drawAxis(colors) {
        const ctx = setup(axisCanvas, geo.width, PLOT_AXIS);
        ctx.fillStyle = colors.muted;
        ctx.textAlign = "center";
        ticks().forEach(({ stamp, step }) => {
            const date = new Date(stamp * 1000);
            const midnight = ((date.getHours() === 0) && (date.getMinutes() === 0));
            ctx.fillText(((step >= 86400) || midnight) ? dayText(date) : timeText(stamp, false), xOf(stamp),
                PLOT_AXIS / 2);
        });
        if ((hoverTime !== null) && (!drag)) {
            // Time of the mouse position
            const text = timeText(hoverTime, true);
            const width = (ctx.measureText(text).width + 10);
            const x = Math.max(geo.x0 + (width / 2), Math.min(geo.x1 - (width / 2), xOf(hoverTime)));
            ctx.fillStyle = colors.strong;
            ctx.fillRect(x - (width / 2), 2, width, PLOT_AXIS - 4);
            ctx.fillStyle = colors.bg;
            ctx.fillText(text, x, PLOT_AXIS / 2);
        }
        ctx.textAlign = "left";
    }
    function drawOverview(colors) {
        const ctx = setup(overCanvas, geo.width, PLOT_OVERVIEW);
        const x0 = geo.x0;
        const x1 = geo.x1;
        const most = Math.max(1, ...data.bins);
        const binWidth = ((x1 - x0) / PLOT_BINS);
        ctx.fillStyle = colors.zebra;
        ctx.fillRect(x0, 2, x1 - x0, PLOT_OVERVIEW - 4);
        ctx.fillStyle = colors.muted;
        data.bins.forEach((num, ix) => {
            if (num) {
                const h = (Math.sqrt(num / most) * (PLOT_OVERVIEW - 8));
                ctx.fillRect(x0 + (ix * binWidth), PLOT_OVERVIEW - 4 - h, Math.max(1, binWidth - 0.5), h);
            }
        });
        const [w0, w1] = shown();
        const full = (data.t1 - data.t0);
        const wa = (x0 + (((w0 - data.t0) / full) * (x1 - x0)));
        const wb = (x0 + (((w1 - data.t0) / full) * (x1 - x0)));
        ctx.fillStyle = ("rgba(" + colors.accent + ", 0.16)");
        ctx.fillRect(wa, 1, Math.max(3, wb - wa), PLOT_OVERVIEW - 2);
        ctx.strokeStyle = ("rgb(" + colors.accent + ")");
        ctx.lineWidth = 1.5;
        ctx.strokeRect(wa, 1, Math.max(3, wb - wa), PLOT_OVERVIEW - 2);
        // Edge handles
        ctx.fillStyle = ("rgb(" + colors.accent + ")");
        [wa, wb].forEach((x) => ctx.fillRect(x - 2, (PLOT_OVERVIEW / 2) - 6, 4, 12));
        ctx.textAlign = "right";
        ctx.fillStyle = colors.muted;
        ctx.fillText("all", x0 - 6, PLOT_OVERVIEW / 2);
        ctx.textAlign = "left";
        geo.over = { wa, wb };
    }
    // Line bands (zebra, hover, selection) and labels
    function drawRows(ctx, colors) {
        const selRow = (selected ? data.rows.get(selected.key) : undefined);
        for (let row = geo.first; row <= geo.last; row++) {
            const item = data.series[row];
            const y = rowY(row);
            const isHover = (hover && (hover.row === row));
            const isSel = (row === selRow);
            if (isHover || isSel) {
                ctx.fillStyle = ("rgba(" + colors.accent + ", " + (isHover ? 0.16 : 0.09) + ")");
                ctx.fillRect(0, y, geo.width, PLOT_ROW);
            } else if (row % 2) {
                ctx.fillStyle = colors.zebra;
                ctx.fillRect(0, y, geo.width, PLOT_ROW);
            }
            const indent = (item.sub ? 10 : 0);
            ctx.font = (((isHover || isSel) ? "600 " : "") + "12px " + getComputedStyle(document.body).fontFamily);
            ctx.fillStyle = ((item.sub && (!isHover) && (!isSel)) ? colors.muted : colors.text);
            ctx.fillText(fit(ctx, item.label, geo.labelWidth - indent - 4), indent, y + (PLOT_ROW / 2));
        }
        ctx.font = ("12px " + getComputedStyle(document.body).fontFamily);
    }
    function outline(ctx, x, y, w, h, color, width) {
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        ctx.strokeRect(x - (width / 2), y - (width / 2), w + width, h + width);
    }
    function clip(ctx) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(geo.x0, 0, geo.x1 - geo.x0, geo.height);
        ctx.clip();
    }
    function drawCursor(ctx, colors) {
        if ((hoverTime === null) || drag) {
            return;
        }
        const x = (Math.round(xOf(hoverTime)) + 0.5);
        ctx.strokeStyle = colors.muted;
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, geo.height);
        ctx.stroke();
        ctx.setLineDash([]);
    }
    function drawNow(ctx, colors) {
        const [w0, w1] = shown();
        if ((data.now >= w0) && (data.now <= w1)) {
            const x = (Math.round(xOf(data.now)) + 0.5);
            ctx.strokeStyle = colors.text;
            ctx.setLineDash([3, 3]);
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, geo.height);
            ctx.stroke();
            ctx.setLineDash([]);
        }
    }

    // Version 1: timeline
    function segment(item, ix) {
        const [w0, w1] = shown();
        const end = endOf(item, ix);
        if ((end <= w0) || (item.points[ix][0] >= w1)) {
            return null;
        }
        const xa = Math.max(geo.x0 - 2, xOf(item.points[ix][0]));
        const xb = Math.min(geo.x1 + 2, xOf(end));
        return { x: xa, w: Math.max(1.5, xb - xa) };
    }
    function drawTimeline(ctx, colors) {
        const [w0] = shown();
        clip(ctx);
        for (let row = geo.first; row <= geo.last; row++) {
            const item = data.series[row];
            const y = rowY(row);
            for (let ix = Math.max(0, pointAt(item, w0)); ix < item.points.length; ix++) {
                const seg = segment(item, ix);
                if (!seg) {
                    if (item.points[ix][0] >= shown()[1]) {
                        break;
                    }
                    continue;
                }
                ctx.fillStyle = PLOT_COLORS[stateOf(item.points[ix])];
                ctx.fillRect(seg.x, y + 3, seg.w, PLOT_ROW - 6);
            }
        }
        drawNow(ctx, colors);
        const mark = (pick, color, width) => {
            const row = (pick ? (("row" in pick) ? pick.row : data.rows.get(pick.key)) : undefined);
            if ((row === undefined) || (pick.ix === undefined) || (row < geo.first) || (row > geo.last)) {
                return;
            }
            const seg = segment(data.series[row], pick.ix);
            if (seg) {
                outline(ctx, seg.x, rowY(row) + 3, seg.w, PLOT_ROW - 6, color, width);
            }
        };
        mark(selected, ("rgb(" + colors.accent + ")"), 2);
        mark(hover, colors.strong, 1.5);
        ctx.restore();
    }

    // Version 2: heatmap
    function heatGrid() {
        const [w0, w1] = shown();
        const step = stepSize(9);
        const start = (Math.floor(w0 / step) * step);
        return { step, start, count: Math.ceil((w1 - start) / step) };
    }
    // For each step: the worst state of the points in it (num: number), else the state from before (faint)
    function cellsOf(item, grid) {
        const cells = [];
        // Last point before the first cell, -1 if none
        let last = pointAt(item, grid.start);
        while ((last >= 0) && (item.points[last][0] >= grid.start)) {
            last -= 1;
        }
        for (let ix = 0; ix < grid.count; ix++) {
            const ta = (grid.start + (ix * grid.step));
            const tb = (ta + grid.step);
            let worst = null;
            let num = 0;
            let next = last;
            while (((next + 1) < item.points.length) && (item.points[next + 1][0] < tb)) {
                next += 1;
                num += 1;
                const state = stateOf(item.points[next]);
                if ((worst === null) || (PLOT_SEVERITY.indexOf(state) < PLOT_SEVERITY.indexOf(worst))) {
                    worst = state;
                }
            }
            cells.push({ ta, tb, num, first: (last + 1), state: (worst || (((last >= 0) && (ta <= data.now)) ?
                stateOf(item.points[last]) : null)) });
            last = next;
        }
        return cells;
    }
    function drawHeatmap(ctx, colors) {
        const grid = heatGrid();
        clip(ctx);
        for (let row = geo.first; row <= geo.last; row++) {
            const y = rowY(row);
            cellsOf(data.series[row], grid).forEach((cell) => {
                if (!cell.state) {
                    return;
                }
                const xa = xOf(cell.ta);
                const xb = xOf(cell.tb);
                ctx.globalAlpha = (cell.num ? 1 : 0.22);
                ctx.fillStyle = PLOT_COLORS[cell.state];
                ctx.fillRect(xa + 0.5, y + 2, Math.max(1, xb - xa - 1), PLOT_ROW - 4);
            });
        }
        ctx.globalAlpha = 1;
        drawNow(ctx, colors);
        const mark = (pick, color, width) => {
            const row = (pick ? (("row" in pick) ? pick.row : data.rows.get(pick.key)) : undefined);
            if ((row === undefined) || (!pick.cell) || (row < geo.first) || (row > geo.last)) {
                return;
            }
            const xa = xOf(pick.cell.ta);
            outline(ctx, xa + 0.5, rowY(row) + 2, Math.max(1, xOf(pick.cell.tb) - xa - 1), PLOT_ROW - 4, color,
                width);
        };
        mark(selected, ("rgb(" + colors.accent + ")"), 2);
        mark(hover, colors.strong, 1.5);
        ctx.restore();
        geo.grid = grid;
    }

    // Version 3: activity
    function activitySteps() {
        const [w0, w1] = shown();
        const step = stepSize(12);
        const start = (Math.floor(w0 / step) * step);
        const steps = Array.from({ length: Math.ceil((w1 - start) / step) }, (_v, ix) => (
            { ta: (start + (ix * step)), tb: (start + ((ix + 1) * step)), points: [] }));
        data.series.forEach((item) => {
            for (let ix = Math.max(0, pointAt(item, w0)); ix < item.points.length; ix++) {
                const point = item.points[ix];
                if (point[0] >= w1) {
                    break;
                }
                const slot = steps[Math.floor((point[0] - start) / step)];
                if (slot && (point[0] >= w0)) {
                    slot.points.push({ item, ix, point });
                }
            }
        });
        return { step, start, steps };
    }
    function drawActivity(ctx, colors) {
        const bottom = (geo.height - 4);
        const high = (bottom - 8);
        const act = activitySteps();
        const most = Math.max(1, ...act.steps.map((slot) => slot.points.length));
        const top = (([1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((size) => (size >= most))) ||
            (Math.ceil(most / 1000) * 1000));
        ctx.fillStyle = colors.muted;
        ctx.strokeStyle = colors.grid;
        ctx.lineWidth = 1;
        ctx.textAlign = "right";
        [0, top / 2, top].forEach((value) => {
            const y = (Math.round(bottom - ((value / top) * high)) + 0.5);
            ctx.fillText(String(value), geo.x0 - 6, y);
            ctx.beginPath();
            ctx.moveTo(geo.x0, y);
            ctx.lineTo(geo.x1, y);
            ctx.stroke();
        });
        ctx.textAlign = "left";
        clip(ctx);
        const bars = act.steps.map((slot) => {
            const xa = xOf(slot.ta);
            const xb = xOf(slot.tb);
            let y = bottom;
            PLOT_STACK.forEach((state) => {
                const num = slot.points.filter(({ point }) => (stateOf(point) === state)).length;
                if (num) {
                    const h = ((num / top) * high);
                    ctx.fillStyle = PLOT_COLORS[state];
                    ctx.fillRect(xa + 1, y - h, Math.max(1, xb - xa - 2), h);
                    y -= h;
                }
            });
            return { x: (xa + 1), w: Math.max(1, xb - xa - 2), y, h: (bottom - y) };
        });
        drawNow(ctx, colors);
        const mark = (pick, color, width) => {
            const bar = ((pick && (pick.step !== undefined)) ? bars[act.steps.findIndex((slot) => (
                slot.ta === pick.step))] : null);
            if (bar && bar.h) {
                outline(ctx, bar.x, bar.y, bar.w, bar.h, color, width);
            }
        };
        mark(selected, ("rgb(" + colors.accent + ")"), 2);
        mark(hover, colors.strong, 1.5);
        ctx.restore();
        geo.act = act;
    }

    // ----- Hit test -----

    function rowAt(y) {
        const row = Math.floor((y + geo.top) / PLOT_ROW);
        return (((row >= 0) && (row < geo.rows)) ? row : null);
    }
    // What is under the mouse: { row, ix, cell, step, text, open() } or null
    function hitAt(x, y) {
        if (!geo) {
            return null;
        }
        if (!geo.lines) {
            if ((x < geo.x0) || (x > geo.x1) || (!geo.act)) {
                return null;
            }
            const slot = geo.act.steps[Math.floor((timeAt(x) - geo.act.start) / geo.act.step)];
            if ((!slot) || (!slot.points.length)) {
                return null;
            }
            const range = (timeText(slot.ta, true) + " – " + timeText(slot.tb, false));
            return { step: slot.ta, text: (range + ": " + countText(slot.points) + "\nClick: the events"),
                open: () => openViews([listView(slot.points, range)]) };
        }
        const row = rowAt(y);
        if (row === null) {
            return null;
        }
        const item = data.series[row];
        if (x < geo.labelWidth) {
            return { row, text: (item.path.join(" → ") + "\n" + item.points.length +
                " state changes\nClick: the changes of this " + (item.kind === "pipeline" ? "pipeline" : "job")),
            open: () => openViews([jobView(item)]) };
        }
        if ((x < geo.x0) || (x > geo.x1)) {
            return null;
        }
        const stamp = timeAt(x);
        if (filter.v === "timeline") {
            const ix = pointAt(item, stamp);
            if ((ix < 0) || (stamp > endOf(item, ix))) {
                return { row, text: (item.path.join(" → ") + "\nno data before the first change"), open: null };
            }
            const point = item.points[ix];
            const end = endOf(item, ix);
            return { row, ix, text: (item.path.join(" → ") + "\n" + stateOf(point) + " " + longTime(point[0]) +
                " – " + ((end >= data.now) ? "now" : longTime(end)) + " (" + dhmsText(end - point[0]) +
                ")\nClick: the event"), open: () => openViews([jobView(item), eventView(item, ix)]) };
        }
        const grid = (geo.grid || heatGrid());
        const cell = cellsOf(item, grid)[Math.floor((stamp - grid.start) / grid.step)];
        if ((!cell) || (!cell.state)) {
            return { row, text: (item.path.join(" → ") + "\nno data"), open: null };
        }
        const range = (timeText(cell.ta, true) + " – " + timeText(cell.tb, false));
        const where = { ta: cell.ta, tb: cell.tb };
        if (!cell.num) {
            return { row, cell: where, text: (item.path.join(" → ") + "\n" + range + ": no change, " + cell.state +
                "\nClick: the changes of this job"), open: () => openViews([jobView(item)]) };
        }
        const points = item.points.slice(cell.first, cell.first + cell.num).map((point, off) => (
            { item, ix: (cell.first + off), point }));
        return { row, cell: where, text: (item.path.join(" → ") + "\n" + range + ": " + countText(points) +
            "\nClick: the " + ((points.length === 1) ? "event" : "events")),
        open: () => openViews((points.length === 1) ? [jobView(item), eventView(item, points[0].ix)] :
            [jobView(item), listView(points, range)]) };
    }
    function countText(points) {
        const counts = {};
        points.forEach(({ point }) => {
            counts[stateOf(point)] = ((counts[stateOf(point)] || 0) + 1);
        });
        return PLOT_SEVERITY.filter((state) => counts[state]).map((state) => (counts[state] + " " + state)).join(", ");
    }

    // ----- Mouse, wheel, keys -----

    function mousePos(ev, target = canvas) {
        const rect = target.getBoundingClientRect();
        return { x: (ev.clientX - rect.left), y: (ev.clientY - rect.top) };
    }
    function showTip(text, ev) {
        const rect = box.getBoundingClientRect();
        tip.textContent = text;
        tip.classList.remove("d-none");
        const x = (ev.clientX - rect.left);
        const y = (ev.clientY - rect.top);
        tip.style.left = (Math.max(0, Math.min(x + 14, box.clientWidth - tip.offsetWidth - 4)) + "px");
        const below = (y + 18);
        tip.style.top = (((below + tip.offsetHeight) > box.clientHeight) ? Math.max(0, y - tip.offsetHeight - 10) :
            below) + "px";
    }
    function hideTip() {
        tip.classList.add("d-none");
    }
    function setHover(found, stamp) {
        hover = (found ? { row: found.row, ix: found.ix, cell: found.cell, step: found.step } : null);
        hoverTime = stamp;
        draw();
    }
    canvas.addEventListener("mousemove", (ev) => {
        if (drag || (!data) || (!geo)) {
            return;
        }
        const { x, y } = mousePos(ev);
        const found = hitAt(x, y);
        const inPlot = ((x >= geo.x0) && (x <= geo.x1));
        canvas.style.cursor = ((found && found.open) ? "pointer" : (inPlot ? "grab" : "default"));
        setHover(found, (inPlot ? timeAt(x) : null));
        if (found) {
            showTip(found.text, ev);
        } else {
            hideTip();
        }
    });
    canvas.addEventListener("mouseleave", () => {
        if (!drag) {
            hideTip();
            setHover(null, null);
        }
    });
    canvas.addEventListener("mousedown", (ev) => {
        if ((!data) || (!geo) || ((ev.button !== 0) && (ev.button !== 1))) {
            return;
        }
        ev.preventDefault();
        scroller.focus({ preventScroll: true });
        const { x, y } = mousePos(ev);
        drag = { mode: (((ev.button === 0) && ev.shiftKey) ? "band" : "pan"), x, y, x2: x, moved: false,
            button: ev.button, start: shown() };
        hideTip();
    });
    overCanvas.addEventListener("mousedown", (ev) => {
        if ((!data) || (!geo) || (!geo.over) || (ev.button !== 0)) {
            return;
        }
        ev.preventDefault();
        const { x } = mousePos(ev, overCanvas);
        const { wa, wb } = geo.over;
        let mode = "over-pan";
        if (Math.abs(x - wa) <= PLOT_EDGE) {
            mode = "over-left";
        } else if (Math.abs(x - wb) <= PLOT_EDGE) {
            mode = "over-right";
        } else if ((x < wa) || (x > wb)) {
            // Outside the window: centre it there
            const [w0, w1] = shown();
            const at = overTime(x);
            setShown(at - ((w1 - w0) / 2), at + ((w1 - w0) / 2));
        }
        drag = { mode, x, x2: x, moved: true, start: shown() };
    });
    overCanvas.addEventListener("mousemove", (ev) => {
        if (drag || (!geo) || (!geo.over)) {
            return;
        }
        const { x } = mousePos(ev, overCanvas);
        const { wa, wb } = geo.over;
        overCanvas.style.cursor = (((Math.abs(x - wa) <= PLOT_EDGE) || (Math.abs(x - wb) <= PLOT_EDGE)) ?
            "ew-resize" : (((x > wa) && (x < wb)) ? "grab" : "pointer"));
    });
    const overTime = (x) => (data.t0 + (((x - geo.x0) / (geo.x1 - geo.x0)) * (data.t1 - data.t0)));
    function onDragMove(ev) {
        if (!drag) {
            return;
        }
        const over = drag.mode.startsWith("over");
        const { x } = mousePos(ev, (over ? overCanvas : canvas));
        drag.x2 = x;
        if (Math.abs(x - drag.x) > PLOT_MOVE) {
            drag.moved = true;
        }
        if (!drag.moved) {
            return;
        }
        const [s0, s1] = drag.start;
        const fullPx = ((data.t1 - data.t0) / (geo.x1 - geo.x0));
        if (drag.mode === "pan") {
            canvas.style.cursor = "grabbing";
            const dt = (((x - drag.x) * (s1 - s0)) / (geo.x1 - geo.x0));
            setShown(s0 - dt, s1 - dt);
        } else if (drag.mode === "over-pan") {
            const dt = ((x - drag.x) * fullPx);
            setShown(s0 + dt, s1 + dt);
        } else if (drag.mode === "over-left") {
            setShown(Math.min(s1 - PLOT_MIN_SPAN, s0 + ((x - drag.x) * fullPx)), s1);
        } else if (drag.mode === "over-right") {
            setShown(s0, Math.max(s0 + PLOT_MIN_SPAN, s1 + ((x - drag.x) * fullPx)));
        } else {
            draw();
        }
    }
    function onDragEnd(ev) {
        if (!drag) {
            return;
        }
        const done = drag;
        drag = null;
        canvas.style.cursor = "grab";
        if (done.mode === "band" && done.moved) {
            const xa = Math.max(geo.x0, Math.min(done.x, done.x2));
            const xb = Math.min(geo.x1, Math.max(done.x, done.x2));
            setShown(timeAt(xa), timeAt(xb));
        } else if ((!done.moved) && (done.button === 0) && (!done.mode.startsWith("over"))) {
            const found = hitAt(done.x, done.y);
            if (found && found.open) {
                selected = { key: ((found.row !== undefined) ? data.series[found.row].key : null), ix: found.ix,
                    cell: found.cell, step: found.step };
                found.open();
            }
            draw();
        } else {
            draw();
        }
        if (ev) {
            ev.preventDefault();
        }
    }
    window.addEventListener("mousemove", onDragMove);
    window.addEventListener("mouseup", onDragEnd);
    canvas.addEventListener("auxclick", (ev) => ev.preventDefault());
    canvas.addEventListener("dblclick", (ev) => {
        if (data && geo) {
            const { x } = mousePos(ev);
            if ((x >= geo.x0) && (x <= geo.x1)) {
                zoomAt(timeAt(x), 0.5);
            }
        }
    });
    function onWheel(ev, target) {
        if ((!data) || (!geo)) {
            return;
        }
        const scale = ((ev.deltaMode === 1) ? 16 : ((ev.deltaMode === 2) ? 400 : 1));
        const dx = ((ev.deltaX || (ev.shiftKey ? ev.deltaY : 0)) * scale);
        if (ev.ctrlKey) {
            ev.preventDefault();
            const { x } = mousePos(ev, target);
            const at = (((x >= geo.x0) && (x <= geo.x1)) ? timeAt(x) : ((shown()[0] + shown()[1]) / 2));
            zoomAt(at, Math.exp(Math.max(-1, Math.min(1, ev.deltaY * scale * 0.002))));
        } else if (dx && (ev.shiftKey || (Math.abs(ev.deltaX) > Math.abs(ev.deltaY)))) {
            ev.preventDefault();
            panBy(pxTime(dx));
        }
    }
    canvas.addEventListener("wheel", (ev) => onWheel(ev, canvas), { passive: false });
    overCanvas.addEventListener("wheel", (ev) => onWheel(ev, overCanvas), { passive: false });
    axisCanvas.addEventListener("wheel", (ev) => onWheel(ev, axisCanvas), { passive: false });
    scroller.addEventListener("scroll", () => {
        hideTip();
        draw();
    });
    scroller.addEventListener("keydown", (ev) => {
        if ((!data) || ev.altKey || ev.ctrlKey || ev.metaKey) {
            return;
        }
        const span = (shown()[1] - shown()[0]);
        const act = { "+": () => zoomCentre(0.5), "=": () => zoomCentre(0.5), "-": () => zoomCentre(2),
            ArrowLeft: () => panBy(-0.2 * span), ArrowRight: () => panBy(0.2 * span),
            0: () => setShown(data.t0, data.t1), Home: () => setShown(data.t0, data.t1), Escape: closePanel }[ev.key];
        if (act) {
            ev.preventDefault();
            act();
        }
    });
    const resizer = new ResizeObserver(() => draw());
    resizer.observe(box);
    window.addEventListener("resize", draw);

    // ----- Legend -----

    el("plot_legend").replaceChildren(...PLOT_SEVERITY.map((state) => {
        const item = node("span", undefined, "plot-key");
        const swatch = node("span", undefined, "plot-swatch");
        swatch.style.background = PLOT_COLORS[state];
        item.append(swatch, state);
        return item;
    }));

    // ----- Details panel: a stack of views (job, event list, event) -----

    function node(tag, text, cls) {
        const item = document.createElement(tag);
        if (text !== undefined) {
            item.textContent = text;
        }
        if (cls) {
            item.className = cls;
        }
        return item;
    }
    function stateBadge(state) {
        const badge = node("span", state, "badge plot-state");
        badge.style.background = PLOT_COLORS[state];
        return badge;
    }
    function sourceTitle(name) {
        const btn = document.querySelector("#plot_sources [data-src='" + name + "']");
        return (btn ? btn.textContent.trim() : name);
    }
    function openViews(views) {
        stack = views;
        showView();
        if (window.innerWidth < 1200) {
            panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
    }
    function pushView(next) {
        stack.push(next);
        showView();
    }
    function closePanel() {
        stack = [];
        selected = null;
        showView();
        draw();
    }
    async function showView() {
        const top = stack[stack.length - 1];
        panel.classList.toggle("plot-idle", !top);
        el("plot_back").classList.toggle("d-none", stack.length < 2);
        el("plot_back").title = ((stack.length > 1) ? ("Back to " + stack[stack.length - 2].title) : "");
        el("plot_panel_close").classList.toggle("d-none", !top);
        el("plot_panel_title").textContent = (top ? top.title : "Details");
        const body = el("plot_panel_body");
        if (!top) {
            body.replaceChildren(node("p", "Point to a line, a cell or a bar. Click: its changes and the events " +
                "that changed the state.", "text-body-secondary mb-0"));
            return;
        }
        body.replaceChildren(node("span", "Loading…", "text-body-secondary"));
        const parts = await top.render();
        if ((current === view) && (stack[stack.length - 1] === top)) {
            body.replaceChildren(...parts);
        }
    }
    el("plot_back").addEventListener("click", () => {
        if (stack.length > 1) {
            stack.pop();
            const top = stack[stack.length - 1];
            if (top.select) {
                selected = top.select;
            }
            showView();
            draw();
        }
    });
    el("plot_panel_close").addEventListener("click", closePanel);
    // Make a line visible (scroll) and a time (pan)
    function reveal(item, stamp) {
        const row = data.rows.get(item.key);
        if ((row !== undefined) && geo && geo.lines) {
            const y = (row * PLOT_ROW);
            if ((y < scroller.scrollTop) || ((y + PLOT_ROW) > (scroller.scrollTop + scroller.clientHeight))) {
                scroller.scrollTop = Math.max(0, y - (scroller.clientHeight / 2));
            }
        }
        const [w0, w1] = shown();
        if ((stamp !== undefined) && ((stamp < w0) || (stamp > w1))) {
            panBy(stamp - ((w0 + w1) / 2));
        }
    }
    // Table rows of points: time, state, job (optional); hover highlights the point, click opens its event
    function pointTable(points, withJob) {
        const table = node("table", undefined, "table table-sm table-hover small mb-0 plot-list");
        const body = node("tbody");
        points.slice(0, PLOT_LIST).forEach(({ item, ix, point }) => {
            const tr = node("tr");
            const cell = node("td");
            cell.append(stateBadge(stateOf(point)));
            tr.append(node("td", longTime(point[0]), "text-nowrap"), cell);
            if (withJob) {
                tr.append(node("td", item.path.slice(1).join(" › ")));
            }
            tr.addEventListener("mouseenter", () => {
                hover = { row: data.rows.get(item.key), ix, cell: null };
                draw();
            });
            tr.addEventListener("mouseleave", () => {
                hover = null;
                draw();
            });
            tr.addEventListener("click", () => {
                selected = { key: item.key, ix };
                reveal(item, point[0]);
                pushView(eventView(item, ix));
                draw();
            });
            body.append(tr);
        });
        table.append(body);
        const parts = [table];
        if (points.length > PLOT_LIST) {
            parts.push(node("p", ("First " + PLOT_LIST + " of " + points.length + " events"),
                "small text-body-secondary mt-2 mb-0"));
        }
        return parts;
    }
    function links(item, url, source) {
        const row = node("p", undefined, "mt-2 mb-0 d-flex flex-wrap gap-2 small");
        if ((typeof url === "string") && /^https?:\/\//.test(url)) {
            const link = node("a", ("Open in " + sourceTitle(source)));
            link.href = url;
            link.target = "_blank";
            link.rel = "noopener";
            row.append(link);
        }
        const hash = eventsHash(item.path);
        if (hash) {
            const link = node("a", "Events of this job");
            link.href = (webRoot + hash);
            row.append(link);
        }
        const only = node("a", ((item.kind === "pipeline") ? "Plot this pipeline and its jobs" : "Plot only this job"));
        only.href = "#";
        only.addEventListener("click", (ev) => {
            ev.preventDefault();
            drillTo({ q: ("\"" + item.path.slice(1).join(" ") + "\""), kind: "", failed: false });
        });
        row.append(only);
        return row;
    }
    // Job: its state changes in the time range
    function jobView(item) {
        return { title: item.path.slice(1).join(" › "), select: { key: item.key },
            render: () => {
                const head = node("p", undefined, "mb-2");
                const lastPoint = item.points[item.points.length - 1];
                head.append(node("span", ((item.kind === "pipeline") ? "Pipeline" : "Job") + ", now "),
                    stateBadge(stateOf(lastPoint)), node("span", (", " + item.points.length + " changes")));
                const points = item.points.map((point, ix) => ({ item, ix, point })).reverse();
                return [head, ...pointTable(points, false), links(item, null, null)];
            } };
    }
    // Events of a cell or time step
    function listView(points, range) {
        return { title: (range + ", " + points.length + " events"), render: () => pointTable(points, true) };
    }
    // One event: the cause of a state change
    function eventView(item, ix) {
        const point = item.points[ix];
        return { title: (stateOf(point) + " " + timeText(point[0], true)), select: { key: item.key, ix },
            render: async () => {
                const before = ((ix > 0) ? stateOf(item.points[ix - 1]) : null);
                let answer = null;
                try {
                    const res = await fetch((apiRoot + "/log/" + point[2]), { cache: "no-store" });
                    answer = (res.ok ? await res.json() : null);
                } catch (err) {
                    console.log("Event failed:", err);
                }
                const head = node("p", undefined, "mb-1");
                head.append(node("span", (item.path.slice(1).join(" › ") + ": "), "fw-medium"),
                    stateBadge(stateOf(point)));
                const when = node("p", (longTime(point[0]) + (before ? (", was " + before) :
                    ", first change in the time range")), "mb-2 text-body-secondary");
                // Previous and next change of the same job
                const nav = node("div", undefined, "btn-group btn-group-sm mb-2");
                [["‹ Previous change", ix - 1], ["Next change ›", ix + 1]].forEach(([text, to]) => {
                    const btn = node("button", text, "btn btn-outline-secondary");
                    btn.type = "button";
                    btn.disabled = ((to < 0) || (to >= item.points.length));
                    btn.addEventListener("click", () => {
                        selected = { key: item.key, ix: to };
                        reveal(item, item.points[to][0]);
                        stack[stack.length - 1] = eventView(item, to);
                        showView();
                        draw();
                    });
                    nav.append(btn);
                });
                const parts = [head, when, nav];
                if (!answer) {
                    parts.push(node("p", "Event not found: the backend was restarted. Load the plot again.",
                        "text-body-secondary mb-0"));
                    return parts;
                }
                const table = node("table", undefined, "table table-sm small mb-0 plot-event");
                const body = node("tbody");
                Object.entries(answer.entry).forEach(([key, value]) => {
                    if ((key === "time") || (value === null) || (value === "")) {
                        return;
                    }
                    const tr = node("tr");
                    tr.append(node("th", key), node("td", String(value)));
                    body.append(tr);
                });
                table.append(body);
                parts.push(table, links(item, answer.entry.url, answer.source));
                return parts;
            } };
    }

    // ----- Start, live updates, hash, close -----

    applyParams(parseHash(location.hash).params);
    showView();
    load();
    view.onUpdate = () => {
        const wait = ((lastLoad + PLOT_RELOAD) - Date.now());
        if (wait <= 0) {
            load();
        } else if (!loadTimer) {
            loadTimer = setTimeout(load, wait);
        }
    };
    view.onParams = (params) => {
        applyParams(params);
        zoom = null;
        scroller.scrollTop = 0;
        load();
    };
    view.onClose = () => {
        clearTimeout(loadTimer);
        clearTimeout(searchTimer);
        cancelAnimationFrame(frame);
        if (loadCtrl) {
            loadCtrl.abort();
        }
        resizer.disconnect();
        window.removeEventListener("resize", draw);
        window.removeEventListener("mousemove", onDragMove);
        window.removeEventListener("mouseup", onDragEnd);
    };
}
