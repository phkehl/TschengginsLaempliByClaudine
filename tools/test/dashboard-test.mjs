// Dashboard test in headless Chrome (Chrome DevTools protocol)
//
// Usage: node --experimental-websocket tools/test/dashboard-test.mjs [<base URL>] [<screenshot dir>]
//
// Needs a local test backend (tools/test/local-backend.sh, default base URL http://127.0.0.1:8099, generic token
// "localtok") and headless Chrome with remote debugging on port 9333 (see device-page-test.mjs). The test adds 80
// generic jobs, then checks: all tiles in the scroll area (the page does not scroll), a long name on two lines, a test
// job that changes to FAIL moves to the front with the FAIL animation and a toast, FAIL -> PASS gives the "fixed"
// animation, running and waiting jobs move, the filter and the search in the hash, the kiosk mode (?full=1), the sticky
// jobs (config dialog, after a tab change), the jobs of a zoomed GitLab pipeline (chips, live, tap on a chip, back with
// Escape or a tap), that the page uses no browser storage and that a new version (data-version) reloads the page. Only
// for a test backend: it refuses other hosts than 127.0.0.1 and localhost. Exit code 1 if a check fails.

import fs from "node:fs";

const base = (process.argv[2] || "http://127.0.0.1:8099").replace(/\/$/, "");
const shots = process.argv[3] || "";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) {
    console.error("Only for a local test backend: " + base);
    process.exit(2);
}
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const proxy = { "X-Forwarded-User": "editor" };
const setField = (name, field, value) => fetch(base + "/web/test/" + name, {
    method: "POST", headers: proxy, body: new URLSearchParams({ [field]: value }), redirect: "manual" });
const setTest = (name, result) => setField(name, "result", result);
for (let i = 1; i <= 80; i++) {
    await fetch(base + "/api/generic/dash-project-" + (i % 5) + "/build/job" + i + "?status=STOPPED&result=" +
        ((i % 4) ? "PASS" : "FAIL"), { method: "POST", headers: { "X-Laempli-Token": "localtok" } });
}
const longName = "dash-a-very-long-project-name-with-many-words-for-the-two-line-wrap-test";
await fetch(base + "/api/generic/" + encodeURIComponent(longName) + "/main/build?status=STOPPED&result=PASS",
    { method: "POST", headers: { "X-Laempli-Token": "localtok" } });
await setTest("test03", "PASS");

// Chrome tab
const target = await (await fetch("http://127.0.0.1:9333/json/new?about:blank", { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0;
const waiting = {};
let onEvent = null;
ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && waiting[data.id]) {
        waiting[data.id](data.result);
        delete waiting[data.id];
    } else if (data.method && onEvent) {
        onEvent(data);
    }
};
await new Promise((ok) => { ws.onopen = ok; });
const send = (method, params = {}) => new Promise((ok) => {
    id++;
    waiting[id] = ok;
    ws.send(JSON.stringify({ id, method, params }));
});
const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true })).result.value;
const shot = async (name) => {
    if (shots) {
        const res = await send("Page.captureScreenshot", { format: "png" });
        fs.writeFileSync(shots + "/dashboard-" + name + ".png", Buffer.from(res.data, "base64"));
    }
};
let failed = 0;
const check = (text, ok) => {
    console.log((ok ? "ok   " : "FAIL ") + text);
    failed += (ok ? 0 : 1);
};
const firstTile = () => js("(() => { const el = document.querySelector('#dash_grid .dash-tile'); " +
    "return el ? { title: el.title, cls: el.className } : null; })()");

await send("Network.enable");
await send("Network.setExtraHTTPHeaders", { headers: proxy });
await send("Emulation.setDeviceMetricsOverride", { width: 1300, height: 800, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: base + "/web/dashboard" });
await sleep(1500);
const fit = await js(`(() => {
    const box = document.getElementById("dash_scroll");
    return { tiles: box.querySelectorAll(".dash-tile").length, more: !!box.querySelector(".dash-more"),
        bottom: Math.round(box.getBoundingClientRect().bottom), height: innerHeight,
        inner: box.scrollHeight - box.clientHeight, page: document.documentElement.scrollHeight - innerHeight };
})()`);
check("all tiles in the scroll area, the page does not scroll (" + JSON.stringify(fit) + ")",
    (fit.tiles === 91) && (!fit.more) && (fit.bottom <= fit.height) && (fit.inner > 0) && (fit.page <= 0));
const counts = await js(`Object.fromEntries(Array.from(document.querySelectorAll("#dash_states [data-state]"),
    (btn) => [btn.dataset.state || "all", Number(btn.querySelector(".dash-count").textContent)]))`);
check("state buttons with counts (" + JSON.stringify(counts) + ")", (counts.all === 91) && (counts.failing === 20));
const wrapCheck = async (text) => {
    const wrap = await js(`(() => {
        const el = Array.from(document.querySelectorAll("#dash_grid .dash-tile"))
            .find((tile) => tile.querySelector(".dash-name").textContent === ${JSON.stringify(longName)});
        if (!el) { return null; }
        const name = el.querySelector(".dash-name");
        const line = parseFloat(getComputedStyle(name).lineHeight);
        const sub = el.querySelector(".dash-sub").getBoundingClientRect();
        return { lines: Math.round(name.clientHeight / line), cut: name.scrollHeight > name.clientHeight,
            inside: sub.bottom <= el.getBoundingClientRect().bottom - 2 };
    })()`);
    check("long name" + text + ": two lines, cut, path line inside the tile (" + JSON.stringify(wrap) + ")",
        (wrap !== null) && (wrap.lines === 2) && wrap.cut && wrap.inside);
};
await wrapCheck("");
await shot("normal");

await js("window.liveMarker = 1");
await setTest("test03", "FAIL");
await sleep(1200);
let tile = await firstTile();
check("test job FAIL: first tile, red, fail animation", tile.title.startsWith("test → test03") &&
    tile.cls.includes("text-bg-danger") && tile.cls.includes("fx-fail"));
check("test job FAIL: toast", (await js("document.getElementById('dash_toasts').innerText")).includes("FAIL: test"));
check("live update without page load", (await js("window.liveMarker")) === 1);
await shot("fail");
await setTest("test03", "PASS");
await sleep(800);
tile = await firstTile();
check("FAIL -> PASS: green, fixed animation", tile.title.startsWith("test → test03") &&
    tile.cls.includes("text-bg-success") && tile.cls.includes("fx-fixed"));

await setField("test04", "status", "RUNNING");
await setField("test06", "status", "WAITING");
await sleep(1500);
const anim = await js(`(() => {
    const tiles = Array.from(document.querySelectorAll("#dash_grid .dash-tile"));
    const run = tiles.find((el) => el.title.startsWith("test → test04"));
    const wait = tiles.find((el) => el.title.startsWith("test → test06"));
    const style = (el, name) => (el ? getComputedStyle(el)[name] : "");
    return { run: (style(run, "backgroundImage").slice(0, 15) + " " + style(run, "animationName")),
        wait: style(wait, "animationName") };
})()`);
check("running: moving stripes, waiting: pulse (" + JSON.stringify(anim) + ")",
    anim.run.startsWith("linear-gradient") && anim.run.includes("progress-bar-stripes") &&
    anim.wait.includes("dash-wait"));
await shot("running");

// Tap on a tile: zoomed copy in the centre with details; tap again, Escape, another tile (one at a time)
const zooms = () => js("document.querySelectorAll('.dash-zoom').length");
const tapTile = (name) => js(`(() => { const el = Array.from(document.querySelectorAll("#dash_grid .dash-tile"))
    .find((tile) => tile.title.endsWith(" → ${name}")); el.click(); return !!el; })()`);
await tapTile("test03");
await sleep(1200);
const zoomed = await js(`(() => {
    const el = document.querySelector(".dash-zoom");
    const r = el.getBoundingClientRect();
    return { path: el.querySelector(".dash-zoom-path").textContent, info: el.querySelector(".dash-info").innerText,
        centre: [Math.round(r.left + (r.width / 2)), innerWidth / 2], z: getComputedStyle(el).zIndex,
        tiles: document.querySelectorAll("#dash_grid .dash-tile").length };
})()`);
check("tap: zoomed tile in the centre with details (" + JSON.stringify(zoomed) + ")",
    (zoomed.path === "test → test03") && zoomed.info.includes("last change") &&
    (Math.abs(zoomed.centre[0] - zoomed.centre[1]) < 2) && (zoomed.tiles > 10));
await shot("zoom");
await tapTile("test04");
await sleep(600);
check("tap on another tile: one zoomed tile, the new one", ((await zooms()) === 1) &&
    ((await js("document.querySelector('.dash-zoom .dash-zoom-path').textContent")) === "test → test04"));
await js("document.querySelector('.dash-zoom').click(); 1");
await sleep(600);
check("tap on the zoomed tile: closed", (await zooms()) === 0);
await tapTile("test03");
await sleep(600);
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await sleep(600);
check("Escape: closed", (await zooms()) === 0);

// Selectors: state, kind (local backend: no pipelines), in the hash
const tilesAll = (test) => js("Array.from(document.querySelectorAll('#dash_grid .dash-tile')).every(" + test + ")");
await js("document.querySelector('#dash_states [data-state=failing]').click(); 1");
await sleep(300);
check("state Failing: only red tiles, in the hash", (await tilesAll("(el) => el.classList.contains('text-bg-danger')"))
    && (await js("location.hash")).includes("state=failing"));
await js("document.querySelector('#dash_states [data-state=running]').click(); 1");
await sleep(300);
check("state Failing + Running", (await js("location.hash")).includes("state=failing%2Crunning") &&
    (await tilesAll("(el) => (el.classList.contains('text-bg-danger') || " +
        "el.classList.contains('progress-bar-striped'))")));
await js("document.querySelector('#dash_states [data-state=\"\"]').click(); 1");
await js("document.querySelector('#dash_kinds [data-kind=pipelines]').click(); 1");
await sleep(300);
check("kind Pipelines: none on the test backend", (await js("document.querySelectorAll('#dash_grid .dash-tile')" +
    ".length")) === 0);
await js("document.querySelector('#dash_kinds [data-kind=jobs]').click(); 1");
await sleep(300);
check("kind Jobs: all, in the hash", ((await js("document.querySelector('#dash_states [data-state=\"\"] .dash-count')" +
    ".textContent")) === "91") && (await js("location.hash")).includes("kind=jobs"));
await shot("selectors");

await js("location.hash = '#/dashboard?src=test'; 1");
await sleep(500);
check("filter src=test: only test jobs", await js("Array.from(document.querySelectorAll('#dash_grid .dash-tile'))" +
    ".every((el) => el.title.startsWith('test → '))"));
await js(`(() => {
    const input = document.getElementById("dash_search");
    input.value = "test05";
    input.dispatchEvent(new Event("input"));
    return 1;
})()`);
await sleep(300);
const count = await js("document.querySelectorAll('#dash_grid .dash-tile').length");
check("search: one tile, in the hash", (count === 1) && (await js("location.hash")).includes("q=test05"));

await js("location.hash = '#/dashboard?full=1'; 1");
await sleep(600);
check("kiosk: no navbar, black, big tiles", await js("document.body.classList.contains('kiosk') && " +
    "(getComputedStyle(document.querySelector('nav.navbar')).display === 'none') && " +
    "(getComputedStyle(document.body).backgroundColor === 'rgb(0, 0, 0)') && " +
    "document.getElementById('dashboard').classList.contains('dash-big')"));
await wrapCheck(" (big tiles)");
await shot("kiosk");
check("no browser storage", (await js("localStorage.length + sessionStorage.length")) === 0);
await js("location.hash = '#/dashboard'; 1");
await sleep(500);
await js("document.getElementById('dash_scroll').scrollTop = 400; 1");
await sleep(300);
check("scroll area scrolls", (await js("document.getElementById('dash_scroll').scrollTop")) > 300);

// Sticky jobs: config dialog (add, select chain as for the LEDs, order), first tiles with border, saved on the server.
// To the dashboard by the tab (the page attributes come from the loaded page, not from the first one).
await js("location.hash = '#/jobs'; 1");
await sleep(800);
await js("Array.from(document.querySelectorAll('#nav .nav-link')).find((a) => (a.textContent === 'Dashboard'))" +
    ".click(); 1");
await sleep(1000);
check("dashboard by the tab: Add job enabled (edit rights)",
    !(await js("document.getElementById('dash_add').disabled")));
await js("document.getElementById('dash_config_open').click(); 1");
await sleep(600);
// Add a row, then select the names level by level (each change makes the next selects)
const addJob = (path) => js(`(() => {
    document.getElementById("dash_add").click();
    const rows = document.querySelectorAll("#dash_sticky_list li");
    const row = rows[rows.length - 1];
    ${JSON.stringify(path)}.forEach((name, level) => {
        const select = row.querySelectorAll("select")[level];
        select.value = name;
        select.dispatchEvent(new Event("change"));
    });
    return 1;
})()`);
await addJob(["test", "test07"]);
await addJob(["generic", "dash-project-2", "build", "job72"]);
await js(`(() => {
    const input = document.querySelectorAll("#dash_sticky_list .dash-sticky-name")[1];
    input.value = "Nightly 72";
    input.dispatchEvent(new Event("input"));
    return 1;
})()`);
await js("document.querySelectorAll('#dash_sticky_list button[aria-label=Up]')[1].click(); 1");
const rowPaths = await js("Array.from(document.querySelectorAll('#dash_sticky_list li'), (li) => " +
    "Array.from(li.querySelectorAll('select'), (select) => select.value).join('/'))");
check("dialog: two sticky jobs (selects), moved up (" + JSON.stringify(rowPaths) + ")",
    JSON.stringify(rowPaths) === JSON.stringify(["generic/dash-project-2/build/job72", "test/test07"]));
await shot("config");
await js("document.getElementById('dash_config_save').click(); 1");
await sleep(1500);
const stickyTiles = () => js("Array.from(document.querySelectorAll('#dash_grid .dash-tile')).slice(0, 3)" +
    ".map((el) => el.querySelector('.dash-name').textContent + ': ' + el.title + " +
    "(el.classList.contains('dash-sticky') ? ' *' : ''))");
const firstThree = await stickyTiles();
check("saved: first tiles sticky in order (" + JSON.stringify(firstThree) + ")",
    (JSON.stringify(firstThree.slice(0, 2)) === JSON.stringify([
        "Nightly 72: generic → dash-project-2 → build → job72 *", "test07: test → test07 *"])) &&
    (!firstThree[2].endsWith(" *")));
const saved = (await (await fetch(base + "/api/dashboard")).json()).sticky.map((entry) => [entry.path, entry.name]);
check("saved on the server (" + JSON.stringify(saved) + ")", JSON.stringify(saved) ===
    JSON.stringify([[["generic", "dash-project-2", "build", "job72"], "Nightly 72"], [["test", "test07"], ""]]));
await shot("sticky");
// Sticky job without data, from another browser (live update)
await fetch(base + "/api/dashboard", { method: "POST", headers: Object.assign({ "Content-Type": "application/json" },
    proxy), body: JSON.stringify({ sticky: [["generic", "nothing", "yet"]] }) });
await sleep(1500);
const unknown = await js("(() => { const el = document.querySelector('#dash_grid .dash-tile'); " +
    "return el.title + ' ' + el.className; })()");
check("sticky job without data: first, unknown (" + unknown + ")", unknown.startsWith("generic → nothing → yet") &&
    unknown.includes("dash-sticky") && unknown.includes("text-bg-secondary"));
check("no 'NaN' times", !(await js("document.getElementById('dash_grid').innerText")).includes("NaN"));
await fetch(base + "/api/dashboard", { method: "POST", headers: Object.assign({ "Content-Type": "application/json" },
    proxy), body: JSON.stringify({ sticky: [] }) });

// GitLab pipeline (live): the zoomed tile has its jobs and downstream pipeline as chips; a tap on a chip zooms it
const gitlab = (kind, body) => fetch(base + "/api/gitlab", { method: "POST", headers: { "Content-Type":
    "application/json", "X-Gitlab-Event": kind }, body: JSON.stringify(body) });
const glProject = { id: 42, name: "Foo", path_with_namespace: "g/p42", web_url: "https://gl/g/foo" };
const glPipeline = (pid, status, builds, bridges, parent) => Object.assign({ object_kind: "pipeline",
    object_attributes: { id: pid, iid: 1, name: "Commit", ref: "dash-ref", tag: false, sha: "abc", status,
        source: "push", url: "https://gl/g/foo/-/pipelines/" + pid },
    project: glProject, user: { username: "flip" },
    builds: builds.map(([jid, name, st]) => ({ id: jid, name, stage: "test", status: st })),
    bridges: bridges.map(([jid, name]) => ({ id: jid, name, stage: "x", status: "running", bridge: true })) },
(parent ? { source_pipeline: parent } : {}));
await gitlab("Pipeline Hook", glPipeline(501, "running", [[601, "build", "success"], [602, "test", "running"],
    [603, "deploy", "created"]], [[604, "downstream"]]));
await gitlab("Pipeline Hook", glPipeline(502, "failed", [[605, "image", "failed"]], [],
    { project: { id: 42 }, pipeline_id: 501, job_id: 604 }));
await sleep(1500);
const chips = () => js("Array.from(document.querySelectorAll('.dash-zoom .dash-chip'), (el) => " +
    "el.querySelector('.dash-chip-name').textContent + ' ' + el.className.split(' ')[1])");
await tapTile("dash-ref");
await sleep(800);
let chipList = await chips();
check("pipeline zoomed: jobs as chips, downstream first (" + JSON.stringify(chipList) + ")",
    JSON.stringify(chipList) === JSON.stringify(["↳ downstream text-bg-danger", "build text-bg-success",
        "deploy text-bg-secondary", "test text-bg-secondary"]));
check("running job chip: stripes", await js("getComputedStyle(Array.from(document.querySelectorAll(" +
    "'.dash-zoom .dash-chip')).find((el) => el.textContent.startsWith('test'))).animationName === " +
    "'progress-bar-stripes'"));
await shot("pipeline");
await gitlab("Job Hook", { object_kind: "build", ref: "dash-ref", tag: false, sha: "abc", pipeline_id: 501,
    build_id: 602, build_name: "test", build_stage: "test", build_status: "success", project_id: 42,
    project: glProject, user: { username: "flip" }, commit: { id: 501, status: "running" } });
await sleep(1500);
chipList = await chips();
check("job change: chip updated live (" + JSON.stringify(chipList) + ")", ((await zooms()) === 1) &&
    chipList.includes("test text-bg-success"));
await js("Array.from(document.querySelectorAll('.dash-zoom .dash-chip')).find((el) => " +
    "el.textContent.includes('downstream')).click(); 1");
await sleep(800);
check("tap on a chip: zooms the downstream pipeline with its job", ((await zooms()) === 1) &&
    ((await js("document.querySelector('.dash-zoom .dash-zoom-path').textContent")) ===
        "gitlab → g/p42 → dash-ref → downstream") && (JSON.stringify(await chips()) ===
        JSON.stringify(["image text-bg-danger"])));
const zoomPath = () => js("document.querySelector('.dash-zoom .dash-zoom-path').textContent");
check("child zoom: hint says go back", (await js("Array.from(document.querySelectorAll('.dash-zoom " +
    ".dash-zoom-hint')).pop().textContent")).endsWith("go back"));
await js("Array.from(document.querySelectorAll('.dash-zoom .dash-chip')).find((el) => " +
    "el.textContent.includes('image')).click(); 1");
await sleep(800);
check("tap on a chip again: zooms the job", (await zoomPath()) === "gitlab → g/p42 → dash-ref → downstream → image");
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await sleep(800);
check("Escape: back to the downstream pipeline", ((await zooms()) === 1) &&
    ((await zoomPath()) === "gitlab → g/p42 → dash-ref → downstream"));
await js("document.querySelector('.dash-zoom .dash-zoom-path').click(); 1");
await sleep(800);
check("tap: back to the pipeline", ((await zooms()) === 1) && ((await zoomPath()) === "gitlab → g/p42 → dash-ref"));
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await sleep(600);
check("Escape on the pipeline: closed", (await zooms()) === 0);
await tapTile("test03");
await sleep(600);
check("job zoomed: no chips", ((await zooms()) === 1) && ((await chips()).length === 0));
await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
await sleep(600);

await js("location.hash = '#/jobs'; 1");
await sleep(1000);
check("other view: kiosk off", !(await js("document.body.classList.contains('kiosk')")));

// New version: the server page has another data-version (changed here in one response) -> the page loads again
await js("window.liveMarker = 7; 1");
await send("Fetch.enable", { patterns: [{ urlPattern: "*/web/devices", requestStage: "Request" }] });
onEvent = async (event) => {
    if (event.method !== "Fetch.requestPaused") {
        return;
    }
    onEvent = null;
    const text = (await (await fetch(event.params.request.url, { headers: proxy })).text())
        .replace(/data-version="[0-9a-f]+"/, 'data-version="000000000000"');
    await send("Fetch.fulfillRequest", { requestId: event.params.requestId, responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: "text/html; charset=utf-8" }],
        body: Buffer.from(text).toString("base64") });
    await send("Fetch.disable");
};
await js("location.hash = '#/devices'; 1");
await sleep(2500);
check("new version: page loaded again, same view", ((await js("window.liveMarker")) === undefined) &&
    ((await js("location.hash")) === "#/devices") && (await js("!!document.getElementById('devices') || " +
    "!!document.querySelector('main[data-view], main .card')")));

console.log(failed ? (failed + " checks failed") : "all checks passed");
process.exit(failed ? 1 : 0);
