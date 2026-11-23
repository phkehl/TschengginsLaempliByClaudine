// Web interface: single page app
//
// Each view is a server page (/web/<route>) with the view in its <main>. The script gets the page and replaces <main>.
// The URL has the view in the hash: /web#/<route>, e.g. /web#/jobs, /web#/device/<ID>. The table views also have the
// search and the sort order (?q=<text>&sort=<column>.<asc|desc>,...), the device view the selected LED (?led=<n>) or
// project row (?row=<n>).
// A /web/<route> page shows its view at once, and the URL changes to the hash form.

const main = document.querySelector("main");
const webRoot = (document.body.dataset.root + "/web");
const defaultRoute = "/devices";

// Current view: route, live update topics, optional functions (onParams: new hash parameters, same route)
let view = { route: null, topics: [], dirty: () => false, onUpdate: null, onParams: null, onClose: null };
let currentHash = "";
let loading = null;
let leaving = false;

// Relative times (d:hh:mm:ss, e.g. "1:30:02", "0:08", same as fmt_dhms() in web.py), with the server clock
let timeOffset = (Number(document.body.dataset.now) - (Date.now() / 1000));
function dhmsText(seconds) {
    let rest = Math.max(0, Math.floor(seconds));
    const parts = [];
    for (const size of [86400, 3600, 60, 1]) {
        if ((parts.length > 0) || (rest >= size) || (size <= 60)) {
            const num = String(Math.floor(rest / size));
            parts.push((parts.length > 0) ? num.padStart(2, "0") : num);
            rest = (rest % size);
        }
    }
    return parts.join(":");
}
function updateTimes() {
    document.querySelectorAll(".reltime").forEach((el) => {
        el.textContent = dhmsText(((Date.now() / 1000) + timeOffset) - Number(el.dataset.time));
    });
}

// Job popovers of the info icons (data-job-tip: HTML). Hover (or focus) shows it. It stays open while the mouse is
// on the icon or on the popover, with a margin (TIP_MARGIN), so the link in it can be clicked. Escape closes it.
const TIP_MARGIN = 16;  // [px] around icon and popover
let openTip = null;  // icon of the open popover
function initJobTip(el) {
    new bootstrap.Popover(el, { trigger: "manual", html: true, content: el.dataset.jobTip, title: "",
        customClass: "job-tip", placement: "bottom" });
    el.addEventListener("mouseenter", () => openJobTip(el));
    el.addEventListener("focus", () => openJobTip(el));
    el.addEventListener("blur", (event) => {
        const tip = bootstrap.Popover.getInstance(el)?.tip;
        if (!(tip && event.relatedTarget && tip.contains(event.relatedTarget))) {
            closeJobTip();
        }
    });
}
function openJobTip(el) {
    if (openTip === el) {
        return;
    }
    closeJobTip();
    openTip = el;
    bootstrap.Popover.getInstance(el)?.show();
}
function closeJobTip() {
    const el = openTip;
    openTip = null;
    if (el) {
        bootstrap.Popover.getInstance(el)?.hide();
    }
}
// Close when the mouse is away from icon and popover
document.addEventListener("mousemove", (event) => {
    if (!openTip) {
        return;
    }
    const near = (el) => {
        if (!el) {
            return false;
        }
        const rect = el.getBoundingClientRect();
        return ((event.clientX >= (rect.left - TIP_MARGIN)) && (event.clientX <= (rect.right + TIP_MARGIN)) &&
            (event.clientY >= (rect.top - TIP_MARGIN)) && (event.clientY <= (rect.bottom + TIP_MARGIN)));
    };
    if (!(near(openTip) || near(bootstrap.Popover.getInstance(openTip)?.tip))) {
        closeJobTip();
    }
});
document.addEventListener("keydown", (event) => {
    if ((event.key === "Escape") && openTip) {
        closeJobTip();
    }
});

// Bootstrap tooltips (times, LEDs) and job popovers
function initTips(root) {
    root.querySelectorAll("[data-bs-toggle='tooltip']").forEach((el) => new bootstrap.Tooltip(el));
    root.querySelectorAll("[data-job-tip]").forEach(initJobTip);
}
function disposeTips(root) {
    root.querySelectorAll("[data-bs-toggle='tooltip']").forEach((el) => {
        const tip = bootstrap.Tooltip.getInstance(el);
        if (tip) {
            tip.dispose();
        }
    });
    root.querySelectorAll("[data-job-tip]").forEach((el) => {
        if (el === openTip) {
            openTip = null;
        }
        bootstrap.Popover.getInstance(el)?.dispose();
    });
}

// Clipped table text (td.clip, td.clip-s, .clip-text): tooltip with the full text, only if clipped
document.addEventListener("mouseover", (event) => {
    let el = event.target.closest(".clip-text, td.clip, td.clip-s");
    if (el && el.matches("td.clip") && el.querySelector(".clip-text")) {
        el = null;
    }
    if (!el) {
        return;
    }
    const tip = bootstrap.Tooltip.getInstance(el);
    if (el.scrollWidth <= el.clientWidth) {
        tip?.dispose();
        el.removeAttribute("data-bs-toggle");
        return;
    }
    if (!tip) {
        el.setAttribute("data-bs-toggle", "tooltip");
        el.setAttribute("data-bs-title", el.textContent.replace(/\s+/g, " ").trim());
        new bootstrap.Tooltip(el).show();
    }
});

// Docs view: scroll to the section of the hash (?sec=<heading ID>), also on a new hash
function startDocs() {
    const scroll = (params) => {
        const sec = params.get("sec");
        const el = (sec ? document.querySelector(".doc [id='" + CSS.escape(sec) + "']") : null);
        if (el) {
            el.scrollIntoView();
        }
    };
    scroll(parseHash(location.hash).params);
    view.onParams = scroll;
}

// Option that stops the path at a job with sub-jobs
function stopLabel(source) {
    return ((source === "github") ? "(this workflow)" : ((source === "gitlab") ? "(this pipeline)" : "(this job)"));
}

// Job path as a chain of selects in the element chain (LED config, dashboard sticky jobs): one select per level of the
// job tree ({name: {job, children}}), each select makes the next ones. The first select has "(off)", a job with
// sub-jobs "(this pipeline)" etc.; a name that is not in the tree stays as extra option "(no events)". Calls
// onChange(path) after each change and at the start.
function jobChain(chain, tree, path, disabled, onChange) {
    chain.replaceChildren();
    let children = tree;
    let isJob = false;
    const result = [];
    for (let level = 0; ; level++) {
        const names = Object.keys(children).sort();
        const current = path[level];
        if ((names.length === 0) && (current === undefined)) {
            break;
        }
        const select = document.createElement("select");
        select.className = "form-select form-select-sm";
        select.disabled = disabled;
        const empty = ((level === 0) ? "(off)" : (isJob ? stopLabel(result[0]) : null));
        if (empty !== null) {
            select.add(new Option(empty, ""));
        }
        names.forEach((name) => select.add(new Option(name, name)));
        if (current && !names.includes(current)) {
            select.add(new Option(current + " (no events)", current));
        }
        select.value = ((current !== undefined) ? current : ((empty !== null) ? "" : names[0]));
        select.onchange = () => {
            const values = Array.from(chain.querySelectorAll("select")).slice(0, (level + 1)).map((item) => item.value);
            jobChain(chain, tree, values.slice(0, ((values.indexOf("") < 0) ? values.length : values.indexOf(""))),
                disabled, onChange);
        };
        chain.appendChild(select);
        if (!select.value) {
            break;
        }
        result.push(select.value);
        const node = children[select.value];
        isJob = (node ? node.job : true);
        children = (node ? node.children : {});
    }
    onChange(result);
}

// Job tree ({name: {job, children}}, as Jobs.tree() in jobs.py) from a list of jobs
function jobTree(list) {
    const tree = {};
    list.forEach((job) => {
        let children = tree;
        let node = null;
        job.path.forEach((name) => {
            node = (children[name] || (children[name] = { job: false, children: {} }));
            children = node.children;
        });
        if (node) {
            node.job = true;
        }
    });
    return tree;
}

// Dashboard view: one tile per job, newest first, in a scroll area (the free height of the window; back to the top
// DASH_SCROLL_TIME after the last scroll), at most DASH_MAX ("+N older" in the last cell). The jobs are
// JSON in the page (#dash_jobs); a live update gets the page again and the script compares the jobs: changed tiles
// move to their new place (FLIP), new ones grow, a change flashes, FAIL shakes and glows red, FAIL -> PASS gives a
// green burst, a result change also shows a toast. A tap on a tile zooms it to the centre, above the others
// (details: path, state, times, info of /api/job, link; for a pipeline its jobs and downstream pipelines as chips, a
// tap on a chip zooms that job, a tap on it or Escape goes back); a tap on it, Escape or DASH_ZOOM_TIME closes it.
// Selectors (hash): state (?state=failing,running,waiting), kind (?kind=pipelines or jobs), source (?src=gitlab,...),
// search (?q=<text>); full=1: kiosk (no navbars, black). Fullscreen button: black too. Sticky jobs (server config,
// dialog of the gear button, editors; optional name): first, in their order, border and pin, also without data. The
// screen stays on (wake lock), the cursor hides after 3 s.
const DASH_RESULT = { PASS: "text-bg-success", FAIL: "text-bg-danger", WARN: "text-bg-warning",
    UNKNOWN: "text-bg-secondary" };
const DASH_IDLE = 3000;  // [ms] until the cursor hides
const DASH_ZOOM_TIME = 60000;  // [ms] until a zoomed tile closes
const DASH_SCROLL_TIME = 60000;  // [ms] after the last scroll: back to the top
const DASH_MAX = 400;  // tiles at most ("+N older" for the rest)
function startDashboard(canEdit) {
    const root = document.getElementById("dashboard");
    const grid = document.getElementById("dash_grid");
    const scroller = document.getElementById("dash_scroll");
    const toasts = document.getElementById("dash_toasts");
    const search = document.getElementById("dash_search");
    const motion = (!window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const readJobs = (doc) => JSON.parse(doc.getElementById("dash_jobs").textContent);
    const key = (job) => JSON.stringify(job.path);
    const bad = (result) => ((result === "FAIL") || (result === "WARN"));
    const tiles = new Map();  // key -> tile element
    const more = document.createElement("div");
    more.className = "dash-more";
    const readSticky = (doc) => (JSON.parse(doc.getElementById("dash_sticky").textContent) || []);
    let jobs = readJobs(document);
    let sticky = readSticky(document);
    let filter = { state: [], kind: "", src: [], q: "", full: false };
    let first = true;
    const STATES = { failing: (job) => bad(job.result), running: (job) => (job.status === "RUNNING"),
        waiting: (job) => (job.status === "WAITING") };

    // Sticky entry ({path, name}) of a job key, undefined if not sticky
    const stickyEntry = (k) => sticky.find((entry) => (JSON.stringify(entry.path) === k));

    // Jobs and the sticky ones without data (unknown); sticky first, in their order, then newest first
    function allJobs() {
        const known = new Map(jobs.map((job) => [key(job), job]));
        const first = sticky.map((entry) => (known.get(JSON.stringify(entry.path)) || { path: entry.path,
            result: "UNKNOWN", status: "UNKNOWN", time: null, link: null, linkText: null, pipeline: false }));
        const firstKeys = new Set(first.map(key));
        return [...first, ...jobs.filter((job) => (!firstKeys.has(key(job))))];
    }

    // Jobs after kind, source and search (counts of the state buttons)
    function selected() {
        const words = filter.q.toLowerCase().split(/\s+/).filter((word) => word);
        return allJobs().filter((job) => (((!filter.kind) || ((filter.kind === "pipelines") === !!job.pipeline)) &&
            ((filter.src.length === 0) || filter.src.includes(job.path[0])) &&
            words.every((word) => job.path.join(" ").toLowerCase().includes(word))));
    }

    // Jobs shown: also the state selector (one of the selected states)
    function byState(list) {
        return ((filter.state.length === 0) ? list :
            list.filter((job) => filter.state.some((state) => STATES[state](job))));
    }
    function visible() {
        return byState(selected());
    }

    // Scroll area of the tiles: the free height of the window (the page does not scroll)
    function setHeight() {
        const top = (scroller.getBoundingClientRect().top + window.scrollY);
        scroller.style.height = (Math.max(160, (window.innerHeight - top - (document.fullscreenElement ? 8 : 16))) +
            "px");
    }

    function makeTile() {
        const el = document.createElement("div");
        el.setAttribute("role", "button");
        el.tabIndex = 0;
        el.innerHTML = "<span class='dash-top'><span class='dash-result'></span><span class='badge dash-status'>" +
            "</span><span class='reltime dash-time'></span><span class='dash-pin' title='Sticky'><svg width='14' " +
            "height='14' viewBox='0 0 16 16' fill='currentColor' aria-hidden='true'><path d='M5 1.5h6l-1 1v4.2l2.5 " +
            "2.3v1H8.6V15L8 15.5 7.4 15V10H3.5V9L6 6.7V2.5z'/></svg></span></span><span class='dash-name'></span>" +
            "<span class='dash-sub'></span>";
        el.addEventListener("animationend", (event) => {
            if (["dash-flash", "dash-glow", "dash-burst"].includes(event.animationName)) {
                el.classList.remove("fx-flash", "fx-fail", "fx-fixed");
            }
        });
        return el;
    }

    function fillTile(el, job) {
        const fx = Array.from(el.classList).filter((cls) => cls.startsWith("fx-"));
        el.className = ["dash-tile", (DASH_RESULT[job.result] || DASH_RESULT.UNKNOWN), ...fx].join(" ");
        el.classList.toggle("progress-bar-striped", (job.status === "RUNNING"));
        el.classList.toggle("progress-bar-animated", (job.status === "RUNNING"));
        el.classList.toggle("dash-waiting", (job.status === "WAITING"));
        el.dataset.key = key(job);
        const entry = stickyEntry(el.dataset.key);
        el.classList.toggle("dash-sticky", !!entry);
        el.title = job.path.join(" → ");
        el.querySelector(".dash-result").textContent = job.result;
        el.querySelector(".dash-status").textContent = job.status;
        // Relative time (updateTimes) only with a time
        const time = el.querySelector(".dash-time");
        time.classList.toggle("reltime", !!job.time);
        if (job.time) {
            time.dataset.time = String(job.time);
        } else {
            delete time.dataset.time;
            time.textContent = "";
        }
        // Sticky job with a name: the name, below the path without the source
        const named = (entry && entry.name);
        el.querySelector(".dash-name").textContent = (named ? entry.name : (job.path[1] || job.path[0]));
        el.querySelector(".dash-sub").textContent = ((named ? job.path.slice(1) : job.path.slice(2)).join(" · ") ||
            job.path[0]);
    }

    // Counts in the state buttons (jobs after kind, source and search)
    function summary(list) {
        document.querySelectorAll("#dash_states [data-state]").forEach((btn) => {
            const state = btn.dataset.state;
            btn.querySelector(".dash-count").textContent = String(state ? list.filter(STATES[state]).length :
                list.length);
        });
    }

    // Show the tiles; changes: key -> "new", "flash", "fail" or "fixed" (animations)
    function render(changes) {
        const base = selected();
        const list = byState(base);
        const shown = list.slice(0, DASH_MAX);
        const animate = (motion && (!first));
        const before = new Map();
        if (animate) {
            tiles.forEach((el, k) => before.set(k, el.getBoundingClientRect()));
        }
        const keep = new Set(shown.map(key));
        tiles.forEach((el, k) => {
            if (!keep.has(k)) {
                tiles.delete(k);
            }
        });
        const order = shown.map((job) => {
            const k = key(job);
            let el = tiles.get(k);
            if (!el) {
                el = makeTile();
                tiles.set(k, el);
            }
            fillTile(el, job);
            return el;
        });
        more.textContent = ("+" + (list.length - shown.length) + " older");
        grid.replaceChildren(...order, ...((list.length > shown.length) ? [more] : []));
        summary(base);
        updateTimes();
        if (animate) {
            shown.forEach((job) => {
                const k = key(job);
                const el = tiles.get(k);
                const old = before.get(k);
                const now = el.getBoundingClientRect();
                if (old && ((old.left !== now.left) || (old.top !== now.top))) {
                    el.animate([{ transform: `translate(${old.left - now.left}px, ${old.top - now.top}px)` },
                        { transform: "none" }], { duration: 700, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
                } else if ((!old) && changes.has(k)) {
                    el.animate([{ transform: "scale(0.3)", opacity: 0 }, { transform: "scale(1.08)", opacity: 1,
                        offset: 0.7 }, { transform: "none", opacity: 1 }], { duration: 700, easing: "ease-out" });
                }
            });
        }
        if (!first) {
            changes.forEach((kind, k) => {
                const el = tiles.get(k);
                if (el) {
                    el.classList.remove("fx-flash", "fx-fail", "fx-fixed");
                    void el.offsetWidth;  // restart the animation
                    el.classList.add("fx-" + ((kind === "new") ? "flash" : kind));
                }
            });
        }
        first = false;
    }

    // Zoomed tile: a copy of the tile with the details, fixed in the centre (above the tiles, which go on)
    let zoom = null;  // { key, el, timer, back: keys of the parent zooms }

    // Jobs and downstream pipelines of a pipeline (one level down, all jobs: no selectors), downstream first,
    // then by name
    function children(job) {
        if (!job.pipeline) {
            return [];
        }
        const depth = job.path.length;
        return jobs.filter((item) => ((item.path.length === (depth + 1)) &&
            job.path.every((name, ix) => (item.path[ix] === name))))
            .sort((a, b) => ((Number(!!b.pipeline) - Number(!!a.pipeline)) ||
                a.path[depth].localeCompare(b.path[depth])));
    }
    // State of the children (refresh the zoomed tile on a change)
    const childState = (job) => JSON.stringify(children(job).map((item) => [item.path, item.result, item.status]));

    // Chip of a child in the zoomed tile: colour, stripes, pulse as the tiles; a tap zooms it
    function makeChip(job) {
        const chip = document.createElement("span");
        chip.className = ["dash-chip", (DASH_RESULT[job.result] || DASH_RESULT.UNKNOWN),
            ((job.status === "RUNNING") ? "progress-bar-striped progress-bar-animated" : ""),
            ((job.status === "WAITING") ? "dash-waiting" : "")].join(" ");
        chip.dataset.key = key(job);
        chip.title = (job.result + ", " + job.status);
        const name = document.createElement("span");
        name.className = "dash-chip-name";
        name.textContent = ((job.pipeline ? "↳ " : "") + job.path[job.path.length - 1]);
        const state = document.createElement("span");
        state.className = "badge dash-status";
        state.textContent = job.status;
        chip.append(name, state);
        return chip;
    }

    function fillZoom(el, job, detail) {
        el.className = ["dash-zoom", (DASH_RESULT[job.result] || DASH_RESULT.UNKNOWN),
            ((job.status === "RUNNING") ? "progress-bar-striped progress-bar-animated" : "")].join(" ");
        const head = document.createElement("div");
        head.className = "dash-top";
        head.innerHTML = "<span class='dash-result'></span><span class='badge dash-status'></span>" +
            "<span class='reltime dash-time'></span>";
        head.querySelector(".dash-result").textContent = job.result;
        head.querySelector(".dash-status").textContent = job.status;
        const path = document.createElement("div");
        path.className = "dash-zoom-path";
        path.textContent = job.path.join(" → ");
        const entry = stickyEntry(key(job));
        if (entry && entry.name) {
            const name = document.createElement("div");
            name.className = "dash-zoom-name";
            name.textContent = entry.name;
            path.prepend(name);
        }
        const info = document.createElement("dl");
        info.className = "dash-info";
        const rows = [];
        head.querySelector(".dash-time").classList.toggle("reltime", !!job.time);
        if (job.time) {
            head.querySelector(".dash-time").dataset.time = String(job.time);
            rows.push(["last change", new Date(job.time * 1000).toLocaleString()]);
        }
        if (detail && detail.started) {
            rows.push(["started", new Date(detail.started * 1000).toLocaleString()]);
        }
        Object.entries((detail && detail.info) || {}).forEach((item) => rows.push(item));
        rows.forEach(([name, value]) => {
            const dt = document.createElement("dt");
            dt.textContent = name;
            const dd = document.createElement("dd");
            dd.textContent = String(value);
            info.append(dt, dd);
        });
        const parts = [head, path, info];
        const kids = children(job);
        if (kids.length > 0) {
            const box = document.createElement("div");
            box.className = "dash-kids";
            box.append(...kids.map(makeChip));
            parts.push(box);
        }
        if (!detail) {
            const wait = document.createElement("div");
            wait.className = "dash-zoom-hint";
            wait.textContent = "Loading details ...";
            parts.push(wait);
        }
        if (job.link) {
            const link = document.createElement("a");
            link.className = "btn btn-light dash-zoom-link";
            link.href = job.link;
            link.target = "_blank";
            link.rel = "noopener";
            link.textContent = (job.linkText || "Open link");
            parts.push(link);
        }
        const hint = document.createElement("div");
        hint.className = "dash-zoom-hint";
        const close = ((el.dataset.back === "1") ? "go back" : "close");
        hint.textContent = ((kids.length > 0) ? ("Tap a job to zoom it, tap elsewhere to " + close) :
            ("Tap to " + close));
        parts.push(hint);
        el.replaceChildren(...parts);
        updateTimes();
    }
    async function loadDetail(job) {
        const k = key(job);
        try {
            const res = await fetch((document.body.dataset.root + "/api/job?path=" +
                encodeURIComponent(JSON.stringify(job.path))), { cache: "no-store" });
            const detail = (res.ok ? await res.json() : {});
            if (zoom && (zoom.key === k)) {
                fillZoom(zoom.el, (allJobs().find((item) => (key(item) === k)) || job), detail);
                centre(zoom.el);
            }
        } catch (err) {
            console.log("Job details:", err);
        }
    }
    // Zoomed tile in the centre of the window (again after new content)
    function centre(el) {
        el.style.left = (Math.max(8, ((window.innerWidth - el.offsetWidth) / 2)) + "px");
        el.style.top = (Math.max(8, ((window.innerHeight - el.offsetHeight) / 2)) + "px");
    }
    // Transform from the box "to" to the box "from" (both from getBoundingClientRect)
    const boxMove = (from, to) => (`translate(${from.left - to.left}px, ${from.top - to.top}px) ` +
        `scale(${from.width / to.width}, ${from.height / to.height})`);
    // Zoom a job, grows from its tile (or from the element "from", e.g. a chip; back: keys of the parent zooms)
    function zoomTile(k, from, back = []) {
        const start = (from || tiles.get(k));
        const rect = (start ? start.getBoundingClientRect() : null);
        unzoom();
        const job = allJobs().find((item) => (key(item) === k));
        if ((!rect) || (!job)) {
            return;
        }
        const el = document.createElement("div");
        el.setAttribute("role", "dialog");
        el.setAttribute("aria-label", "Job details");
        el.dataset.back = ((back.length > 0) ? "1" : "0");
        fillZoom(el, job, null);
        el.style.visibility = "hidden";
        root.append(el);
        // Centre, then grow from the tile
        centre(el);
        el.style.visibility = "";
        if (motion) {
            el.animate([{ transform: boxMove(rect, el.getBoundingClientRect()), opacity: 0.6 },
                { transform: "none", opacity: 1 }], { duration: 450, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
        }
        el.addEventListener("click", (event) => {
            const chip = event.target.closest(".dash-chip");
            if (chip) {
                zoomTile(chip.dataset.key, chip, [...back, k]);
            } else if (!event.target.closest("a")) {
                zoomBack();
            }
        });
        zoom = { key: k, el, timer: setTimeout(unzoom, DASH_ZOOM_TIME), back };
        loadDetail(job);
    }
    // Back to the parent pipeline, else close
    function zoomBack() {
        if (zoom && (zoom.back.length > 0)) {
            zoomTile(zoom.back[zoom.back.length - 1], zoom.el, zoom.back.slice(0, -1));
        } else {
            unzoom();
        }
    }
    function unzoom() {
        if (!zoom) {
            return;
        }
        const { el, key: k, timer } = zoom;
        zoom = null;
        clearTimeout(timer);
        const tile = tiles.get(k);
        let anim = null;
        if (motion && tile && tile.isConnected) {
            anim = el.animate([{ transform: "none", opacity: 1 }, { transform: boxMove(tile.getBoundingClientRect(),
                el.getBoundingClientRect()), opacity: 0.4 }], { duration: 350, easing: "ease-in", fill: "forwards" });
        } else if (motion) {
            anim = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 250, fill: "forwards" });
        }
        if (anim) {
            anim.finished.then(() => el.remove(), () => el.remove());
        } else {
            el.remove();
        }
    }
    grid.addEventListener("click", (event) => {
        const tile = event.target.closest(".dash-tile");
        if (tile) {
            if (zoom && (zoom.key === tile.dataset.key)) {
                unzoom();
            } else {
                zoomTile(tile.dataset.key);
            }
        }
    });
    grid.addEventListener("keydown", (event) => {
        if (((event.key === "Enter") || (event.key === " ")) && event.target.closest(".dash-tile")) {
            event.preventDefault();
            event.target.closest(".dash-tile").click();
        }
    });
    const onKey = (event) => {
        if (event.key === "Escape") {
            zoomBack();
        }
    };

    // Config dialog: sticky jobs (draft until Save), each a chain of selects as in the LED config (jobChain)
    const config = document.getElementById("dash_config");
    if (config) {
        const list = document.getElementById("dash_sticky_list");
        const add = document.getElementById("dash_add");
        const status = document.getElementById("dash_config_status");
        const save = document.getElementById("dash_config_save");
        let draft = [];
        let tree = {};
        const button = (text, label, action) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn btn-outline-secondary";
            btn.textContent = text;
            btn.setAttribute("aria-label", label);
            btn.disabled = (!canEdit);
            btn.addEventListener("click", action);
            return btn;
        };
        const move = (ix, to) => {
            if ((to >= 0) && (to < draft.length)) {
                draft.splice(to, 0, draft.splice(ix, 1)[0]);
                showDraft();
            }
        };
        function showDraft() {
            list.replaceChildren(...draft.map((entry, ix) => {
                const item = document.createElement("li");
                item.className = "list-group-item d-flex flex-wrap align-items-center gap-2";
                const chain = document.createElement("div");
                chain.className = "chain flex-grow-1";
                jobChain(chain, tree, entry.path, (!canEdit), (result) => {
                    entry.path = result;
                });
                // Optional name: on the tile instead of the project
                const name = document.createElement("input");
                name.type = "text";
                name.className = "form-control form-control-sm dash-sticky-name";
                name.maxLength = 40;
                name.placeholder = "Name (optional)";
                name.setAttribute("aria-label", "Name");
                name.value = entry.name;
                name.disabled = (!canEdit);
                name.addEventListener("input", () => {
                    entry.name = name.value;
                });
                const group = document.createElement("div");
                group.className = "btn-group btn-group-sm";
                group.append(button("↑", "Up", () => move(ix, (ix - 1))), button("↓", "Down", () => move(ix, (ix + 1))),
                    button("✕", "Remove", () => {
                        draft.splice(ix, 1);
                        showDraft();
                    }));
                item.append(chain, name, group);
                return item;
            }));
            if (!draft.length) {
                const item = document.createElement("li");
                item.className = "list-group-item text-body-secondary";
                item.textContent = "No sticky jobs";
                list.append(item);
            }
        }
        add.disabled = (!canEdit);
        add.addEventListener("click", () => {
            draft.push({ path: [], name: "" });
            showDraft();
        });
        config.addEventListener("show.bs.modal", () => {
            tree = jobTree(jobs);
            draft = sticky.map((entry) => ({ path: [...entry.path], name: entry.name }));
            status.textContent = "";
            save.disabled = (!canEdit);
            showDraft();
        });
        save.addEventListener("click", async () => {
            save.disabled = true;
            let error = null;
            try {
                const res = await fetch((document.body.dataset.root + "/api/dashboard"), { method: "POST",
                    cache: "no-store", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ sticky: draft.filter((entry) => (entry.path.length >= 2)) }) });
                const answer = await res.json().catch(() => ({}));
                if (res.ok) {
                    update(jobs, answer.sticky);
                } else {
                    error = (answer.error || ((res.status === 403) ? "no edit rights" : ("error " + res.status)));
                }
            } catch (err) {
                error = "no connection";
            }
            save.disabled = false;
            if (error) {
                status.textContent = ("Not saved: " + error);
                status.classList.add("text-danger");
            } else {
                bootstrap.Modal.getOrCreateInstance(config).hide();
            }
        });
    }

    // Toast for a result change
    function toast(job, old) {
        const el = document.createElement("div");
        el.className = ("toast border-0 " + (DASH_RESULT[job.result] || DASH_RESULT.UNKNOWN));
        el.setAttribute("role", "status");
        const body = document.createElement("div");
        body.className = "toast-body";
        body.textContent = (job.result + ": " + job.path.join(" · ") + " (was " + old.result + ")");
        el.append(body);
        toasts.prepend(el);
        while (toasts.children.length > 5) {
            toasts.lastElementChild.remove();
        }
        el.addEventListener("hidden.bs.toast", () => el.remove());
        new bootstrap.Toast(el, { delay: 8000 }).show();
    }

    // New jobs (and sticky jobs) from a live update: compare, then show
    function update(next, nextSticky) {
        const old = new Map(allJobs().map((job) => [key(job), job]));
        // Zoomed job and its children (also when not shown as a tile)
        const zoomState = () => {
            const job = (zoom && allJobs().find((item) => (key(item) === zoom.key)));
            return (job ? JSON.stringify([job.result, job.status, job.time, childState(job)]) : "");
        };
        const zoomBefore = zoomState();
        const shownBefore = new Set(visible().map(key));
        const changes = new Map();
        jobs = next;
        sticky = nextSticky;
        const shownNow = new Set(visible().map(key));
        allJobs().forEach((job) => {
            const k = key(job);
            const prev = old.get(k);
            if (!prev) {
                changes.set(k, "new");
            } else if (prev.result !== job.result) {
                changes.set(k, (bad(job.result) ? "fail" : ((bad(prev.result) && (job.result === "PASS")) ? "fixed" :
                    "flash")));
                if (shownNow.has(k)) {
                    toast(job, prev);
                }
            } else if ((prev.status !== job.status) || (prev.time !== job.time)) {
                changes.set(k, "flash");
            }
        });
        changes.forEach((kind, k) => {
            if (!(shownNow.has(k) || shownBefore.has(k))) {
                changes.delete(k);
            }
        });
        render(changes);
        if (zoom && (zoomState() !== zoomBefore)) {
            const job = allJobs().find((item) => (key(item) === zoom.key));
            if (job) {
                fillZoom(zoom.el, job, null);
                loadDetail(job);
            }
        }
    }

    // Filter from the hash (unknown values ignored); kiosk mode
    function applyParams(params) {
        const items = (name, known) => (params.get(name) || "").split(",")
            .filter((item) => (item && known.includes(item)));
        const sources = Array.from(document.querySelectorAll("#dash_sources [data-src]"), (btn) => btn.dataset.src);
        filter = { state: items("state", Object.keys(STATES)), kind: (items("kind", ["pipelines", "jobs"])[0] || ""),
            src: items("src", sources), q: (params.get("q") || ""), full: (params.get("full") === "1") };
        search.value = filter.q;
        const mark = (selector, attr, isActive) => document.querySelectorAll(selector).forEach((btn) => {
            const active = isActive(btn.dataset[attr]);
            btn.classList.toggle("active", active);
            btn.setAttribute("aria-pressed", String(active));
        });
        mark("#dash_states [data-state]", "state", (state) => (state ? filter.state.includes(state) :
            (filter.state.length === 0)));
        mark("#dash_kinds [data-kind]", "kind", (kind) => (kind === filter.kind));
        mark("#dash_sources [data-src]", "src", (src) => (src ? filter.src.includes(src) : (filter.src.length === 0)));
        document.body.classList.toggle("kiosk", filter.full);
        setBig();
    }
    function writeParams() {
        const params = new URLSearchParams();
        if (filter.state.length) {
            params.set("state", filter.state.join(","));
        }
        if (filter.kind) {
            params.set("kind", filter.kind);
        }
        if (filter.src.length) {
            params.set("src", filter.src.join(","));
        }
        if (filter.q) {
            params.set("q", filter.q);
        }
        if (filter.full) {
            params.set("full", "1");
        }
        const query = params.toString();
        history.replaceState(null, "", ("#" + view.route + (query ? ("?" + query) : "")));
        currentHash = location.hash;
    }

    // Kiosk and fullscreen: black, dark theme, bigger tiles
    function setBig() {
        const big = (filter.full || (document.fullscreenElement === root));
        root.classList.toggle("dash-big", big);
        if (big) {
            root.dataset.bsTheme = "dark";
        } else {
            delete root.dataset.bsTheme;
        }
    }

    // Selector buttons: state and source toggle (several, "All": none), kind: one
    const toggle = (list, item) => ((!item) ? [] : (list.includes(item) ? list.filter((other) => (other !== item)) :
        [...list, item]));
    const onSelect = (id, attr, change) => document.getElementById(id).addEventListener("click", (event) => {
        const btn = event.target.closest("[data-" + attr + "]");
        if (!btn) {
            return;
        }
        change(btn.dataset[attr]);
        writeParams();
        applyParams(parseHash(location.hash).params);
        render(new Map());
    });
    onSelect("dash_states", "state", (state) => {
        filter.state = toggle(filter.state, state);
    });
    onSelect("dash_kinds", "kind", (kind) => {
        filter.kind = kind;
    });
    onSelect("dash_sources", "src", (src) => {
        filter.src = toggle(filter.src, src);
    });
    search.addEventListener("input", () => {
        filter.q = search.value.trim();
        writeParams();
        render(new Map());
    });
    document.getElementById("dash_full").addEventListener("click", () => {
        if (document.fullscreenElement) {
            document.exitFullscreen();
        } else if (root.requestFullscreen) {
            root.requestFullscreen().catch((err) => console.log("Fullscreen:", err));
        }
    });

    // Clock, size changes, wake lock, cursor
    const clock = document.getElementById("dash_clock");
    const tick = () => {
        const now = new Date();
        clock.textContent = [now.getHours(), now.getMinutes(), now.getSeconds()]
            .map((num) => String(num).padStart(2, "0")).join(":");
    };
    tick();
    const clockTimer = setInterval(tick, 1000);
    let resizeTimer = null;
    const onResize = () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            setHeight();
            render(new Map());
        }, 150);
    };
    let scrollTimer = null;
    const onScroll = () => {
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(() => scroller.scrollTo({ top: 0, behavior: (motion ? "smooth" : "auto") }),
            DASH_SCROLL_TIME);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    const onFullscreen = () => {
        setBig();
        onResize();
    };
    let lock = null;
    const wake = async () => {
        if ((!("wakeLock" in navigator)) || (document.visibilityState !== "visible")) {
            return;
        }
        try {
            lock = await navigator.wakeLock.request("screen");
        } catch (err) {
            lock = null;
        }
    };
    let idleTimer = null;
    const onMouse = () => {
        document.body.classList.remove("dash-idle");
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => document.body.classList.add("dash-idle"), DASH_IDLE);
    };
    window.addEventListener("resize", onResize);
    document.addEventListener("fullscreenchange", onFullscreen);
    document.addEventListener("visibilitychange", wake);
    document.addEventListener("mousemove", onMouse);
    document.addEventListener("keydown", onKey);
    wake();
    onMouse();

    applyParams(parseHash(location.hash).params);
    setHeight();
    render(new Map());
    view.onUpdate = (doc) => update(readJobs(doc), readSticky(doc));
    view.onParams = (params) => {
        applyParams(params);
        setHeight();
        render(new Map());
    };
    view.onClose = () => {
        clearInterval(clockTimer);
        clearTimeout(scrollTimer);
        clearTimeout(resizeTimer);
        clearTimeout(idleTimer);
        window.removeEventListener("resize", onResize);
        document.removeEventListener("fullscreenchange", onFullscreen);
        document.removeEventListener("visibilitychange", wake);
        document.removeEventListener("mousemove", onMouse);
        document.removeEventListener("keydown", onKey);
        if (zoom) {
            clearTimeout(zoom.timer);
            zoom = null;
        }
        document.body.classList.remove("kiosk", "dash-idle");
        if (lock) {
            lock.release().catch(() => null);
            lock = null;
        }
        if (document.fullscreenElement) {
            document.exitFullscreen().catch(() => null);
        }
    };
}

// Get route and parameters of a hash
function parseHash(hash) {
    const [route, query] = hash.slice(1).split("?");
    return { route: (route.startsWith("/") ? route : defaultRoute), params: new URLSearchParams(query || "") };
}

// Table with sort, search and pages. Search field and number of rows above the table, left; rows per page and page
// buttons (first, previous, next, last) right. When filtered, a link clears the search. No browser storage: a new
// view starts with the defaults (200 rows, first page), search and sort from the hash. Live updates keep all.
// The table can come later (live update).
// Server table (data-server="<rows URL>", e.g. the event logs): the server sorts, searches and gives the rows of one
// page (serverRows()), a live update loads the page again (reloadServer()). Filters (data-filters="<table ID>", see
// tablefilters.html): in front of the search field, in the hash; server tables send them with the rows request,
// the others show the rows with a matching data-<name> attribute.
const liveTables = {};  // id -> DataTable, null if not there yet
const tableFilters = {};  // id -> { groups: { name: { mode, values } }, values: { name: [value, ...] } }
// Public API, not the settings fields (they change between DataTables versions). Server tables do not call it.
DataTable.ext.search.push((settings, _data, index) => {
    const api = new DataTable.Api(settings);
    const filter = tableFilters[api.table().node().id];
    const row = (filter ? api.row(index).node() : null);
    if (!row) {
        return true;
    }
    return Object.entries(filter.values).every(([name, values]) => ((values.length === 0) ||
        values.includes(row.dataset[name])));
});
function sortTable(id) {
    liveTables[id] = null;
    const table = document.getElementById(id);
    if (!table) {
        return;
    }
    const paging = (table.dataset.paging !== "false");
    const server = table.dataset.server;
    const { params } = parseHash(location.hash);
    const options = {};
    const filterBox = document.querySelector("[data-filters='" + id + "']");
    if (filterBox) {
        setFilters(id, params);
    }
    if (server) {
        const columns = table.tHead.rows[0].cells.length;
        Object.assign(options, {
            serverSide: true,
            searchDelay: 400,
            lengthMenu: [100, 200, 500, 1000],
            ajax: (data, callback) => serverRows(server, data, callback, filterParams(id)),
            columns: Array.from({ length: columns }, (_v, ix) => ({ data: (row) => row.cells[ix] })),
            createdRow: (row, data) => {
                row.className = data.cls;
                Array.from(row.cells).forEach((td, ix) => {
                    td.className = data.classes[ix];
                });
            },
            drawCallback: () => {
                initTips(table.tBodies[0]);
                updateTimes();
            },
            // Search and sort from the hash at once (no extra request)
            search: { search: (params.get("q") || "") },
            order: (params.has("sort") ? tableOrder(params.get("sort")) : []),
        });
    }
    const dt = new DataTable("#" + id, {
        autoWidth: false,
        paging,
        pageLength: 200,
        lengthMenu: [[100, 200, 500, 1000, -1], ["100", "200", "500", "1000", "All"]],
        pagingType: "full",
        order: [],
        ...options,
        // With filters: filters and search left, the info right before the pages
        layout: { topStart: (filterBox ? [() => filterBox, "search"] : ["search", "info"]),
            topEnd: (paging ? (filterBox ? ["info", "pageLength", "paging"] : ["pageLength", "paging"]) :
                (filterBox ? ["info"] : null)), bottomStart: null, bottomEnd: null },
        language: {
            search: "",
            searchPlaceholder: "Search",
            lengthMenu: "_MENU_ per page",
            info: "_START_–_END_ of _TOTAL_ _ENTRIES-TOTAL_",
            infoEmpty: "0 entries",
            // Server tables (event logs) and tables with filters: no "show all"
            infoFiltered: ((server || filterBox) ? "of _MAX_" :
                "of _MAX_ (<a href='#' class='dt-show-all'>show all</a>)"),
        },
    });
    dt.table().container().addEventListener("click", (ev) => {
        if (ev.target.closest(".dt-show-all")) {
            ev.preventDefault();
            dt.search("").draw();
        }
    });
    liveTables[id] = dt;
    if (filterBox) {
        filterBox.addEventListener("click", (ev) => {
            const btn = ev.target.closest("[data-filter]");
            if (!btn) {
                return;
            }
            const { values } = tableFilters[id];
            const { filter: name, value } = btn.dataset;
            const now = values[name];
            values[name] = ((!value) ? [] : ({
                one: () => [value],
                toggle: () => (now.includes(value) ? [] : [value]),
            }[btn.dataset.mode] || (() => (now.includes(value) ? now.filter((item) => (item !== value)) :
                now.concat([value]))))());
            markFilters(id);
            if (server) {
                dt.ajax.reload();
            } else {
                dt.draw();
            }
            syncTableHash(dt);
        });
    }
    if ((!server) && (params.has("q") || params.has("sort"))) {
        applyTableParams(dt, params);
    }
    syncTableHash(dt);
    dt.on("search.dt order.dt", () => syncTableHash(dt));
}

// Get table order from the hash parameter (<column>.<asc|desc>,...)
function tableOrder(sort) {
    return sort.split(",").map((item) => {
        const [column, dir] = item.split(".");
        return [Number(column), ((dir === "desc") ? "desc" : "asc")];
    });
}

// Filters of a table from the hash (only values with a button; mode one and toggle: one value)
function setFilters(id, params) {
    const groups = {};
    document.querySelectorAll("[data-filters='" + id + "'] [data-filter]").forEach((btn) => {
        const group = (groups[btn.dataset.filter] = (groups[btn.dataset.filter] ||
            { mode: (btn.dataset.mode || "many"), values: [] }));
        if (btn.dataset.value) {
            group.values.push(btn.dataset.value);
        }
    });
    const values = {};
    Object.entries(groups).forEach(([name, group]) => {
        const found = (params.get(name) || "").split(",").filter((value) => group.values.includes(value));
        values[name] = ((group.mode === "many") ? found : found.slice(0, 1));
    });
    tableFilters[id] = { groups, values };
    markFilters(id);
}
function markFilters(id) {
    const { values } = tableFilters[id];
    document.querySelectorAll("[data-filters='" + id + "'] [data-filter]").forEach((btn) => {
        const now = values[btn.dataset.filter];
        const on = (btn.dataset.value ? now.includes(btn.dataset.value) : (now.length === 0));
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-pressed", String(on));
    });
}
// Request (and hash) parameters of the filters of a table: { name: "value,..." }
function filterParams(id) {
    const params = {};
    Object.entries(tableFilters[id] ? tableFilters[id].values : {}).forEach(([name, values]) => {
        if (values.length) {
            params[name] = values.join(",");
        }
    });
    return params;
}

// Set search and sort order from the hash
function applyTableParams(dt, params) {
    const id = dt.table().node().id;
    if (tableFilters[id]) {
        setFilters(id, params);
    }
    dt.search(params.get("q") || "");
    if (params.has("sort")) {
        dt.order(tableOrder(params.get("sort")));
    }
    dt.draw(false);
}

// Server table: get the rows of a page. The server gives {total, filtered, rows: [<tr> HTML]}. Each row goes to the
// table as {cls: <tr> class, cells: [<td> HTML], classes: [<td> class]} (see sortTable()).
async function serverRows(url, data, callback, extra) {
    const query = new URLSearchParams({ start: data.start, length: data.length, q: (data.search.value || ""),
        sort: data.order.map((item) => (item.column + "." + item.dir)).join(","), ...(extra || {}) });
    let result = { total: 0, filtered: 0, rows: [] };
    try {
        const res = await fetch((url + "?" + query), { cache: "no-store" });
        if (!res.ok) {
            throw new Error("HTTP " + res.status);
        }
        result = await res.json();
    } catch (err) {
        console.log("Rows failed:", err);
    }
    const body = document.createElement("tbody");
    body.innerHTML = result.rows.join("");
    callback({ draw: data.draw, recordsTotal: result.total, recordsFiltered: result.filtered,
        data: Array.from(body.rows).map((tr) => ({ cls: tr.className,
            cells: Array.from(tr.cells).map((td) => td.innerHTML),
            classes: Array.from(tr.cells).map((td) => td.className) })) });
}

// Server table: load the page again (live update), keep page, search and sort. At most one request each
// RELOAD_MIN ms, changes meanwhile give one more.
const RELOAD_MIN = 2000;  // [ms]
const reloads = new WeakMap();  // DataTable -> { busy, again, last }
function reloadServer(dt) {
    const state = (reloads.get(dt) || { busy: false, again: false, last: 0 });
    reloads.set(dt, state);
    if (state.busy) {
        state.again = true;
        return;
    }
    state.busy = true;
    setTimeout(() => {
        state.last = Date.now();
        try {
            dt.ajax.reload(() => {
                state.busy = false;
                if (state.again) {
                    state.again = false;
                    reloadServer(dt);
                }
            }, false);
        } catch (err) {
            // Table gone (other view)
            state.busy = false;
        }
    }, Math.max(0, ((state.last + RELOAD_MIN) - Date.now())));
}

// Put search and sort order into the hash
function syncTableHash(dt) {
    const params = new URLSearchParams();
    if (dt.search()) {
        params.set("q", dt.search());
    }
    const order = dt.order().map((item) => (item[0] + "." + item[1])).join(",");
    if (order) {
        params.set("sort", order);
    }
    Object.entries(filterParams(dt.table().node().id)).forEach(([key, value]) => params.set(key, value));
    const query = params.toString();
    history.replaceState(null, "", ("#" + view.route + (query ? ("?" + query) : "")));
    currentHash = location.hash;
}

// Replace the parts (data-live="<key>") with the ones of the new page (doc). A sorted table (sortTable()) that is in
// both pages gets only the new rows: order, search and scroll position stay.
function swapParts(doc) {
    document.querySelectorAll("[data-live]").forEach((el) => {
        const part = doc.querySelector("[data-live='" + el.dataset.live + "']");
        if (!part) {
            return;
        }
        const table = el.querySelector("table[id]");
        const dt = (table ? liveTables[table.id] : null);
        const newTable = (dt ? part.querySelector("table[id='" + table.id + "']") : null);
        if (newTable && table.dataset.server) {
            reloadServer(dt);
            return;
        }
        if (newTable) {
            disposeTips(table.tBodies[0]);
            dt.clear();
            dt.rows.add(Array.from(newTable.tBodies[0].rows));
            dt.draw(false);
            initTips(table.tBodies[0]);
            return;
        }
        if (el.innerHTML === part.innerHTML) {
            return;
        }
        disposeTips(el);
        if (dt) {
            dt.destroy();
            liveTables[table.id] = null;
        }
        el.innerHTML = part.innerHTML;
        initTips(el);
        Object.keys(liveTables).forEach((id) => {
            if ((liveTables[id] === null) && el.querySelector("table[id='" + id + "']")) {
                sortTable(id);
            }
        });
    });
}

// Ask to discard unsaved changes, then run the action
const discard = new bootstrap.Modal(document.getElementById("discard"));
let discardAction = null;
function askDiscard(action) {
    discardAction = action;
    discard.show();
}
document.getElementById("discard_yes").addEventListener("click", () => {
    discard.hide();
    discardAction();
});

// Remove button of the Jobs page (editors): forget a branch (GitLab ref, GitHub branch), see /api/jobs/remove
const removeModal = document.getElementById("remove_branch");
if (removeModal) {
    const dialog = new bootstrap.Modal(removeModal);
    const error = document.getElementById("remove_error");
    let removePath = null;
    main.addEventListener("click", (event) => {
        const button = event.target.closest(".job-remove");
        if (!button) {
            return;
        }
        removePath = JSON.parse(button.dataset.path);
        document.getElementById("remove_name").textContent = removePath.slice(1).join(" → ");
        document.getElementById("remove_source").textContent = ((removePath[0] === "github") ? "GitHub" : "GitLab");
        error.classList.add("d-none");
        showRemoveJobs(removePath);
        dialog.show();
    });

    // List the jobs of the branch in the dialog (at most REMOVE_LIST)
    const REMOVE_LIST = 12;
    async function showRemoveJobs(path) {
        const list = document.getElementById("remove_jobs");
        const count = document.getElementById("remove_count");
        count.textContent = "These jobs go away:";
        list.replaceChildren();
        let names = [];
        try {
            const res = await fetch((document.body.dataset.root + "/api/jobs"), { cache: "no-store" });
            const jobs = (await res.json()).jobs;
            names = jobs.filter((job) => path.every((name, ix) => (job.path[ix] === name)))
                .map((job) => (job.path.slice(3).join(" → ") || "pipeline"));
        } catch (err) {
            count.textContent = "All jobs of this branch go away.";
            return;
        }
        if (path !== removePath) {
            return;
        }
        count.textContent = ((names.length === 1) ? "This job goes away:" : ("These " + names.length +
            " jobs go away:"));
        names.slice(0, REMOVE_LIST).forEach((name) => {
            const item = document.createElement("li");
            item.textContent = name;
            list.append(item);
        });
        if (names.length > REMOVE_LIST) {
            const item = document.createElement("li");
            item.textContent = ("and " + (names.length - REMOVE_LIST) + " more");
            list.append(item);
        }
    }
    document.getElementById("remove_yes").addEventListener("click", async () => {
        let text = null;
        try {
            const res = await fetch((document.body.dataset.root + "/api/jobs/remove"), { method: "POST",
                cache: "no-store", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: removePath }) });
            if (!res.ok) {
                text = ((await res.json().catch(() => ({}))).error || ("error " + res.status));
            }
        } catch (err) {
            text = "no connection";
        }
        if (text) {
            error.textContent = ("Not removed: " + text);
            error.classList.remove("d-none");
            return;
        }
        dialog.hide();
        refreshView();
    });
}

// Loading animation
function setBusy(busy) {
    document.getElementById("loading").classList.toggle("d-none", !busy);
    main.classList.toggle("busy", busy);
}

// New version of the scripts and styles (deploy): the page (doc) has another data-version. Then load the whole page
// again (same hash), except with unsaved changes (the next page from the server checks again). True: reloads.
const appVersion = document.body.dataset.version;
function newVersion(doc) {
    const version = doc.body.dataset.version;
    if ((!version) || (!appVersion) || (version === appVersion) || view.dirty()) {
        return false;
    }
    console.log("New version " + version + " (was " + appVersion + "): reload");
    leaving = true;
    location.reload();
    return true;
}

// Parse a page, get its view, null if it has none
function parsePage(text) {
    const doc = new DOMParser().parseFromString(text, "text/html");
    return (doc.querySelector("main[data-view]") ? doc : null);
}

// Remove the current view
function closeView() {
    disposeTips(main);
    Object.keys(liveTables).forEach((id) => {
        if (liveTables[id]) {
            liveTables[id].destroy();
        }
        delete liveTables[id];
    });
    if (view.onClose) {
        view.onClose();
    }
    view = { route: view.route, topics: [], dirty: () => false, onUpdate: null, onParams: null, onClose: null };
}

// Show a message instead of a view
function showError(route, text) {
    closeView();
    view.route = route;
    const alert = document.createElement("div");
    alert.className = "alert alert-danger";
    alert.textContent = text;
    main.replaceChildren(alert);
    document.getElementById("subnav").replaceChildren();
}

// Start the view in main. source: its <main>.
function startView(route, source) {
    view.route = route;
    view.topics = JSON.parse(source.dataset.topics || "[]");
    initTips(main);
    if (source.dataset.table) {
        sortTable(source.dataset.table);
    }
    if (source.dataset.view === "device") {
        startDevice(source.dataset.canEdit === "true");
    }
    if (source.dataset.view === "test") {
        startTest();
    }
    if (source.dataset.view === "docs") {
        startDocs();
    }
    if (source.dataset.view === "dashboard") {
        startDashboard(source.dataset.canEdit === "true");
    }
    if (source.dataset.view === "plots") {
        startPlots(source);
    }
    updateTimes();
}

// Get a view and show it
async function loadView(route) {
    if (loading) {
        loading.abort();
    }
    const ctrl = new AbortController();
    loading = ctrl;
    const timer = setTimeout(() => setBusy(true), 150);
    try {
        const res = await fetch((webRoot + route), { cache: "no-store", signal: ctrl.signal });
        const doc = parsePage(await res.text());
        if (!doc) {
            showError(route, ("Cannot show this page: error " + res.status));
            return;
        }
        if (newVersion(doc)) {
            return;
        }
        const source = doc.querySelector("main");
        closeView();
        main.innerHTML = source.innerHTML;
        document.title = doc.title;
        document.querySelector("#nav .navbar-nav").innerHTML = doc.querySelector("#nav .navbar-nav").innerHTML;
        document.getElementById("subnav").innerHTML = (doc.getElementById("subnav")?.innerHTML || "");
        timeOffset = (Number(doc.body.dataset.now) - (Date.now() / 1000));
        window.scrollTo(0, 0);
        bootstrap.Collapse.getInstance(document.getElementById("nav"))?.hide();
        startView(route, source);
    } catch (err) {
        if (err.name !== "AbortError") {
            console.log("Load failed:", err);
            showError(route, "Cannot get this page. Check the connection.");
        }
    } finally {
        clearTimeout(timer);
        if (loading === ctrl) {
            loading = null;
            setBusy(false);
        }
    }
}

// Follow the hash: other view, or other parameters of this view
window.addEventListener("hashchange", () => {
    const hash = location.hash;
    if (hash === currentHash) {
        return;
    }
    const { route, params } = parseHash(hash);
    if (route === view.route) {
        if (loading) {
            loading.abort();
        }
        currentHash = hash;
        Object.values(liveTables).forEach((dt) => {
            if (dt) {
                applyTableParams(dt, params);
            }
        });
        if (view.onParams) {
            view.onParams(params);
        }
        return;
    }
    if (view.dirty()) {
        history.replaceState(null, "", currentHash);
        askDiscard(() => {
            view.dirty = () => false;
            location.hash = hash;
        });
        return;
    }
    currentHash = hash;
    loadView(route);
});

// Hash of the events tab for a job path, null if none. Same as events_query() in web.py.
function eventsHash(path) {
    if (!["gitlab", "github", "generic"].includes(path[0])) {
        return null;
    }
    const query = path.slice(1).map((name) => name.replaceAll("\"", "")).map((name) => (
        name.includes(" ") ? ("\"" + name + "\"") : name)).join(" ");
    return ("#/" + path[0] + (query ? ("?q=" + encodeURIComponent(query)) : ""));
}

// Links: views without page load, other pages ask first if there are unsaved changes
document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    const newTab = (link && ((link.target === "_blank") || event.ctrlKey || event.metaKey || event.shiftKey ||
        (event.button !== 0)));
    if ((!link) || newTab || event.defaultPrevented) {
        return;
    }
    const url = new URL(link.href);
    const hashLink = ((url.pathname === webRoot) && url.hash.startsWith("#/"));
    if ((url.origin === location.origin) && (hashLink || url.pathname.startsWith(webRoot + "/"))) {
        event.preventDefault();
        const hash = (hashLink ? url.hash : ("#" + url.pathname.slice(webRoot.length)));
        if (hash === ("#" + view.route)) {
            refreshView();
        } else if (view.dirty()) {
            askDiscard(() => {
                view.dirty = () => false;
                location.hash = hash;
            });
        } else {
            location.hash = hash;
        }
    } else if (view.dirty()) {
        event.preventDefault();
        askDiscard(() => {
            leaving = true;
            location.href = link.href;
        });
    }
});
window.addEventListener("beforeunload", (event) => {
    if ((!leaving) && view.dirty()) {
        event.preventDefault();
        event.returnValue = "";
    }
});

// Get the view again and swap the parts. Then view.onUpdate(doc), if given.
let refreshBusy = false;
let refreshAgain = false;
async function refreshView() {
    if (refreshBusy) {
        refreshAgain = true;
        return;
    }
    refreshBusy = true;
    try {
        do {
            refreshAgain = false;
            const current = view;
            if (!current.route) {
                break;
            }
            const res = await fetch((webRoot + current.route), { cache: "no-store" });
            const doc = (res.ok ? parsePage(await res.text()) : null);
            if ((!doc) || (current !== view) || newVersion(doc)) {
                break;
            }
            timeOffset = (Number(doc.body.dataset.now) - (Date.now() / 1000));
            swapParts(doc);
            if (view.onUpdate) {
                view.onUpdate(doc);
            }
            updateTimes();
            blinkDevices([]);
        } while (refreshAgain);
    } catch (err) {
        console.log("Live update failed:", err);
    }
    refreshBusy = false;
}

// Data from a Lämpli (topic "device:<ID>"): the live badge and the status badges of that device (data-dev-status)
// blink. A live update replaces the badges: the new ones continue the blink (negative animation delay).
const BLINK_TIME = 800;  // [ms], same as .blink in laempli.css
const blinkSeen = {};  // device ID -> time of its last data [ms]
function blink(el, since) {
    el.classList.remove("blink");
    el.style.animationDelay = ("-" + Math.round(Date.now() - since) + "ms");
    void el.offsetWidth;
    el.classList.add("blink");
}
function blinkDevices(ids) {
    const now = Date.now();
    ids.forEach((id) => {
        blinkSeen[id] = now;
    });
    const live = document.querySelector("#live .badge");
    if (live && (ids.length > 0)) {
        blink(live, now);
    }
    document.querySelectorAll("[data-dev-status]").forEach((el) => {
        const since = blinkSeen[el.dataset.devStatus];
        if (since && ((now - since) < BLINK_TIME) && (ids.includes(el.dataset.devStatus) ||
                (!el.classList.contains("blink")))) {
            blink(el, since);
        }
    });
}

// Live updates: WebSocket /ws/web (behind the proxy below the same path, else on its own port). When one of the topics
// of the view changes (e.g. "devices", "device:<ID>", "jobs"), get the view again. Without WebSocket: the same at each
// reconnect try (up to 30 s).
function startLive() {
    const badge = document.getElementById("live");
    const port = document.body.dataset.wsPort;
    const proto = ((location.protocol === "https:") ? "wss:" : "ws:");
    const url = (port ? (proto + "//" + location.hostname + ":" + port + "/ws/web") :
        (proto + "//" + location.host + document.body.dataset.root + "/ws/web"));
    let retry = 1000;
    let offline = false;

    function setLive(ok) {
        badge.classList.remove("d-none");
        badge.innerHTML = (ok ? "<span class='badge text-bg-success'>live</span>" :
            "<span class='badge text-bg-secondary' title='No live updates, reconnecting'>offline</span>");
    }

    function connect() {
        const ws = new WebSocket(url);
        ws.onopen = () => {
            setLive(true);
            retry = 1000;
            // Changes while offline
            if (offline) {
                offline = false;
                refreshView();
            }
        };
        ws.onmessage = (ev) => {
            const msg = JSON.parse(ev.data);
            if (msg.event === "changed") {
                blinkDevices(msg.data.filter((topic) => topic.startsWith("device:")).map((topic) => topic.slice(7)));
            }
            if ((msg.event === "changed") && msg.data.some((topic) => view.topics.includes(topic))) {
                refreshView();
            } else if (msg.event === "time") {
                timeOffset = (msg.data.time - (Date.now() / 1000));
            }
        };
        // Offline: refresh at each retry (e.g. no WebSocket on the proxy)
        ws.onclose = () => {
            setLive(false);
            if (offline) {
                refreshView();
            }
            offline = true;
            setTimeout(connect, retry);
            retry = Math.min(retry * 2, 30000);
        };
    }
    connect();
}

// Device view: LED config (or project rows) and settings forms
//
// LED config: left the LEDs as on the device (tiles, or lines of the project rows), right the fields of one LED (or
// row). A click on an LED selects it: its fields show, the others are hidden (they stay in the form). An LED with
// unsaved changes has a dashed border. All fields end in _<index>, e.g. name_0, project_2.
function startDevice(canEdit) {
    let tree = JSON.parse(document.getElementById("tree").textContent);

    // "saved": form as saved (null until the chains are built)
    const forms = [
        { form: document.getElementById("ledform"), button: document.getElementById("save"),
            cancel: document.getElementById("cancel"), saved: null },
        { form: document.getElementById("settings"), button: document.getElementById("settings_save"), saved: null },
    ];
    const visual = document.getElementById("led_visual");
    const panels = Array.from(forms[0].form.querySelectorAll(".led-fields"));
    const selectParam = (visual.classList.contains("led-rows") ? "row" : "led");
    let selected = 0;

    function formState(item) {
        return new URLSearchParams(new FormData(item.form)).toString();
    }

    function isChanged(item) {
        return ((item.saved !== null) && (formState(item) !== item.saved));
    }

    // Save and cancel buttons only with changes
    function updateSave() {
        forms.forEach((item) => {
            const off = (item.saving || (!canEdit) || (!isChanged(item)));
            if (item.button) {
                item.button.disabled = off;
            }
            if (item.cancel) {
                item.cancel.disabled = off;
            }
        });
        updateMarks();
    }

    // Indexes of the LEDs (or rows) with unsaved changes
    function changedLeds() {
        const result = new Set();
        if (forms[0].saved === null) {
            return result;
        }
        const saved = new URLSearchParams(forms[0].saved);
        const now = new URLSearchParams(formState(forms[0]));
        new Set([...saved.keys(), ...now.keys()]).forEach((key) => {
            const match = key.match(/_(\d+)$/);
            if (match && (saved.get(key) !== now.get(key))) {
                result.add(Number(match[1]));
            }
        });
        return result;
    }

    // Mark changed LEDs: dashed border, badge in the fields
    function updateMarks() {
        const changed = changedLeds();
        visual.querySelectorAll(".led-tile, .led-row").forEach((el) => {
            el.classList.toggle("changed", changed.has(Number(el.dataset.ix)));
        });
        panels.forEach((panel) => {
            panel.querySelector(".led-changed").hidden = (!changed.has(Number(panel.dataset.ix)));
        });
    }

    // Show the fields of one LED (or row). Put it in the hash, except at the start.
    function selectLed(ix, setHash) {
        selected = ((Number.isInteger(ix) && (ix >= 0) && (ix < panels.length)) ? ix : 0);
        panels.forEach((panel) => {
            panel.hidden = (Number(panel.dataset.ix) !== selected);
        });
        visual.querySelectorAll(".led-tile, .led-row").forEach((el) => {
            el.classList.toggle("selected", (Number(el.dataset.ix) === selected));
        });
        visual.querySelectorAll("button[data-ix]").forEach((el) => {
            el.setAttribute("aria-pressed", String(Number(el.dataset.ix) === selected));
        });
        updateFindLabel();
        if (setHash) {
            const { params } = parseHash(location.hash);
            params.set(selectParam, String(selected + 1));
            history.replaceState(null, "", ("#" + view.route + "?" + params.toString()));
            currentHash = location.hash;
        }
    }

    // LED test button "Find LED n" (or row n): the selected one
    function updateFindLabel() {
        const num = forms[0].form.querySelector(".led-find-num");
        if (num) {
            num.textContent = String(selected + 1);
        }
    }

    function hashLed(params) {
        return (Number(params.get(selectParam) || "1") - 1);
    }

    // Link below the fields of a row: events of its job
    function setEventsLink(ix, path) {
        const link = document.getElementById("events_" + ix);
        const hash = eventsHash(path);
        link.hidden = (!hash);
        if (hash) {
            link.href = (webRoot + hash);
        } else {
            link.removeAttribute("href");
        }
    }

    // Links of the project rows: value is <source>:<project>
    function updateRowLinks() {
        document.querySelectorAll("select[name^='project_']").forEach((select) => {
            const colon = select.value.indexOf(":");
            setEventsLink(select.name.slice("project_".length), ((colon < 0) ? [] :
                [select.value.slice(0, colon), select.value.slice(colon + 1)]));
        });
    }

    // Make the selects of a row for a path (jobChain)
    function buildChain(ix, path) {
        jobChain(document.getElementById("chain_" + ix), tree, path, (!canEdit), (result) => {
            document.getElementById("path_" + ix).value = JSON.stringify(result);
            setEventsLink(ix, result);
            updateSave();
        });
    }

    function fieldNames(form) {
        return Array.from(form.querySelectorAll("input[name], select[name]")).map((el) => el.name).join(",");
    }

    // Give a form the values of the new page
    function applyForm(item, doc) {
        doc.getElementById(item.form.id).querySelectorAll("input[name], select[name]").forEach((el) => {
            // Selects: new options first (e.g. sounds of the device)
            if (el.tagName === "SELECT") {
                item.form.elements[el.name].innerHTML = el.innerHTML;
            }
            item.form.elements[el.name].value = el.value;
        });
        tree = JSON.parse(doc.getElementById("tree").textContent);
        for (let ix = 0; item.form.querySelector("#chain_" + ix); ix++) {
            const path = doc.getElementById("chain_" + ix).dataset.path;
            document.getElementById("chain_" + ix).dataset.path = path;
            buildChain(ix, JSON.parse(path) || []);
        }
        updateRowLinks();
        item.saved = formState(item);
    }

    // Revert: saved values back, then get the newest
    function restoreForm(item) {
        const saved = new URLSearchParams(item.saved);
        item.form.querySelectorAll("input[name], select[name]").forEach((el) => {
            if (saved.has(el.name)) {
                el.value = saved.get(el.name);
            }
        });
        for (let ix = 0; document.getElementById("chain_" + ix); ix++) {
            buildChain(ix, JSON.parse(saved.get("path_" + ix) || "[]"));
        }
        updateRowLinks();
        setStatus(item, "", true);
        updateSave();
        refreshView();
    }

    // Message next to the save button
    function setStatus(item, text, ok) {
        const status = item.form.querySelector(".form-status");
        status.textContent = text;
        status.classList.toggle("text-success", ok);
        status.classList.toggle("text-danger", !ok);
    }

    // Save one form, the other keeps its unsaved changes
    async function saveForm(item) {
        item.saving = true;
        updateSave();
        setStatus(item, "", true);
        const timer = setTimeout(() => setBusy(true), 150);
        try {
            const res = await fetch(item.form.action, {
                method: "POST", cache: "no-store", body: new URLSearchParams(new FormData(item.form)) });
            const doc = (((res.ok) || (res.status === 400)) ? parsePage(await res.text()) : null);
            if (!doc) {
                setStatus(item, ((res.status === 403) ? "Not saved: no edit rights" : ("Not saved: error " +
                    res.status)), false);
            } else if (res.ok) {
                swapParts(doc);
                updateFindLabel();
                if (fieldNames(doc.getElementById(item.form.id)) === fieldNames(item.form)) {
                    applyForm(item, doc);
                }
                setStatus(item, "Saved", true);
                setTimeout(() => setStatus(item, "", true), 3000);
            } else {
                const error = doc.getElementById(item.form.id).querySelector(".alert-danger");
                setStatus(item, (error ? error.textContent : "Not saved"), false);
                // Show the LED (or row) of the error
                const match = (error ? error.textContent.match(/(?:LED|row) (\d+):/) : null);
                if (match && (item === forms[0])) {
                    selectLed((Number(match[1]) - 1), true);
                }
            }
        } catch (err) {
            console.log("Save failed:", err);
            setStatus(item, "Not saved: no connection", false);
        }
        clearTimeout(timer);
        setBusy(false);
        item.saving = false;
        updateSave();
    }

    // POST to the API, returns the error text (null: OK)
    async function postApi(url, data) {
        try {
            const res = await fetch(url, { method: "POST", cache: "no-store",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
            if (res.ok) {
                return null;
            }
            const answer = await res.json().catch(() => ({}));
            return (answer.error || ("error " + res.status));
        } catch (err) {
            console.log("Request failed:", err);
            return "no connection";
        }
    }

    // Sound test: plays the selected sound, saved or not
    const apiDevice = (document.body.dataset.root + "/api/device/" + view.route.split("/")[2]);
    forms[0].form.addEventListener("click", async (event) => {
        const button = event.target.closest(".sound-test");
        if (!button) {
            return;
        }
        const sound = button.closest(".input-group").querySelector("select").value;
        if (!sound) {
            setStatus(forms[0], "No sound selected", false);
            return;
        }
        const error = await postApi((apiDevice + "/sound"), { sound: sound });
        setStatus(forms[0], (error ? ("Sound not played: " + error) : ("Sound sent: " + sound)), (!error));
    });

    // LED test buttons: the device shows it LED_REMOTE_MS (60 s), the badge comes with its report
    forms[0].form.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-led-test]");
        if (!button) {
            return;
        }
        const data = { mode: button.dataset.ledTest };
        if (data.mode === "find") {
            data[selectParam] = selected;
        }
        const error = await postApi((apiDevice + "/led"), data);
        setStatus(forms[0], (error ? ("LED test failed: " + error) : ("LED test sent: " +
            button.textContent.replace(/\s+/g, " ").trim())), (!error));
    });

    // Firmware update: the live update shows the progress bar instead of the button
    const firmwareCard = document.getElementById("details");
    firmwareCard.addEventListener("click", async (event) => {
        const button = event.target.closest("#firmware_update");
        if (!button) {
            return;
        }
        button.disabled = true;
        const error = await postApi((apiDevice + "/update"), {});
        const status = firmwareCard.querySelector(".form-status");
        status.textContent = (error ? ("Update not started: " + error) : "");
        status.classList.toggle("text-danger", !!error);
        button.disabled = (!error);
    });

    // LED arrangement: click selects
    visual.addEventListener("click", (event) => {
        const el = event.target.closest("[data-ix]");
        if (el) {
            selectLed(Number(el.dataset.ix), true);
        }
    });
    forms[0].cancel.addEventListener("click", () => restoreForm(forms[0]));
    selectLed(hashLed(parseHash(location.hash).params), false);
    view.onParams = (params) => selectLed(hashLed(params), false);

    // Start: make the selects of all rows from the saved paths
    for (let ix = 0; document.getElementById("chain_" + ix); ix++) {
        buildChain(ix, JSON.parse(document.getElementById("chain_" + ix).dataset.path) || []);
    }
    updateRowLinks();
    forms[0].form.addEventListener("change", updateRowLinks);
    forms.forEach((item) => {
        item.saved = formState(item);
        item.form.addEventListener("input", updateSave);
        item.form.addEventListener("change", updateSave);
        item.form.addEventListener("submit", (event) => {
            event.preventDefault();
            saveForm(item);
        });
    });
    updateSave();

    view.dirty = () => forms.some(isChanged);

    // Live update: forms without unsaved changes get the new values. Other fields (e.g. first settings from the
    // device): get the view again.
    view.onUpdate = (doc) => {
        const newForms = forms.map((item) => doc.getElementById(item.form.id));
        if (newForms.some((form, ix) => ((!form) || (fieldNames(form) !== fieldNames(forms[ix].form))))) {
            if (!view.dirty()) {
                loadView(view.route);
            }
            return;
        }
        forms.forEach((item) => {
            if ((!item.saving) && (!isChanged(item))) {
                applyForm(item, doc);
            }
        });
        updateSave();
        updateFindLabel();
    };
}

// Test view: the result and status buttons select the values of a job (row: data-job, data-result, data-status:
// the values now), Send sends both in one event, Cancel goes back to the values now. Selected values not sent yet
// stay over live updates.
function startTest() {
    const current = view;
    const card = document.getElementById("test_jobs");
    const pending = {};  // job name -> { result, status }: selected, not sent
    // Buttons, form values and Send button of a row
    function mark(row) {
        const name = row.dataset.job;
        const form = row.querySelector("form");
        const want = (pending[name] || {});
        let changed = false;
        ["result", "status"].forEach((field) => {
            const value = (want[field] || row.dataset[field]);
            form.elements[field].value = value;
            changed = (changed || (value !== row.dataset[field]));
            row.querySelectorAll("[data-field='" + field + "']").forEach((btn) => {
                const on = (btn.dataset.value === value);
                btn.classList.toggle("btn-" + btn.dataset.cls, on);
                btn.classList.toggle("btn-outline-" + btn.dataset.cls, !on);
                btn.setAttribute("aria-pressed", String(on));
            });
        });
        if (!changed) {
            delete pending[name];
        }
        const send = form.querySelector(".test-send");
        send.disabled = (!changed);
        send.classList.toggle("btn-primary", changed);
        send.classList.toggle("btn-outline-secondary", !changed);
        form.querySelector(".test-cancel").disabled = (!changed);
    }
    function markAll() {
        card.querySelectorAll("tr[data-job]").forEach(mark);
    }
    card.addEventListener("click", (event) => {
        const cancel = event.target.closest(".test-cancel");
        if (cancel) {
            delete pending[cancel.closest("tr").dataset.job];
            mark(cancel.closest("tr"));
            return;
        }
        const btn = event.target.closest("[data-field]");
        if ((!btn) || btn.disabled) {
            return;
        }
        const row = btn.closest("tr");
        pending[row.dataset.job] = Object.assign((pending[row.dataset.job] || {}),
            { [btn.dataset.field]: btn.dataset.value });
        mark(row);
    });
    card.addEventListener("submit", async (event) => {
        event.preventDefault();
        const status = document.getElementById("test_status");
        status.textContent = "";
        const row = event.target.closest("tr");
        try {
            const res = await fetch(event.target.action, {
                method: "POST", cache: "no-store", body: new URLSearchParams(new FormData(event.target)) });
            const doc = (res.ok ? parsePage(await res.text()) : null);
            if (!res.ok) {
                status.textContent = ((res.status === 403) ? "Not saved: no edit rights" : ("Not saved: error " +
                    res.status));
            } else if (doc && (current === view)) {
                delete pending[row.dataset.job];
                swapParts(doc);
                markAll();
                updateTimes();
            }
        } catch (err) {
            console.log("Save failed:", err);
            status.textContent = "Not saved: no connection";
        }
    });
    view.onUpdate = () => markAll();
    view.dirty = () => (Object.keys(pending).length > 0);
    markAll();
}

// Start: the view of the hash, else of this page
function startApp() {
    const pageRoute = location.pathname.slice(webRoot.length);
    const route = (location.hash.startsWith("#/") ? parseHash(location.hash).route : (pageRoute || defaultRoute));
    const hash = (location.hash.startsWith("#/") ? location.hash : ("#" + route));
    history.replaceState(null, "", (webRoot + hash));
    currentHash = location.hash;
    if (main.dataset.view && (route === pageRoute)) {
        startView(route, main);
    } else {
        main.replaceChildren();
        view.route = route;
        loadView(route);
    }
    setInterval(updateTimes, 1000);
    startLive();
}
startApp();
