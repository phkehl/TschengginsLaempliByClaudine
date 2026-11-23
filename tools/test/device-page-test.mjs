// Device page test in headless Chrome (Chrome DevTools protocol)
//
// Usage: node --experimental-websocket tools/test/device-page-test.mjs [<base URL>] [<screenshot dir>]
//
// Needs a local test backend (tools/test/local-backend.sh, default base URL http://127.0.0.1:8099, WebSocket server on
// the next port) and headless Chrome with remote debugging on port 9333:
//
//     google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/laempli-chrome about:blank &
//
// The test makes a fake device chlotz-0000aa (WebSocket /ws/device with reports, as the firmware; LED 1: a test job)
// and checks the sound test, the LED test buttons, the firmware button, the LED arrangement (click shows the fields of
// one LED, marks of changed LEDs, Revert) and the LED and settings forms: Save buttons, the "Discard unsaved changes?"
// modal, pending settings, live updates (the device has the settings, LED config from another browser, devices table)
// without page reload, and a bad LED value, then the page of a user without edit rights and of a device editor. It
// sends the X-Forwarded-User header (editor) of the reverse proxy. Only for a test backend: it refuses other hosts than
// 127.0.0.1 and localhost.
// Exit code 1 if a check fails.

import fs from "node:fs";

const base = (process.argv[2] || "http://127.0.0.1:8099").replace(/\/$/, "");
const shots = process.argv[3] || "";
const dev = "chlotz-0000aa";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) {
    console.error("Only for a local test backend: " + base);
    process.exit(2);
}

// Device: settings report and stream (connected)
const settings = { name: "Test", dim_timeout: 60, dim_level: 20, saver_timeout: 60, led_brightness: 64,
    anim_running: 1, anim_waiting: 0 };
const options = { led_brightness: [[16, "very dim"], [64, "medium"]], anim_running: ["a", "b"],
    anim_waiting: ["x", "y"], melodies: ["Bond:7"], led_test: true };
// Header of the reverse proxy
const proxy = (user) => ({ "X-Forwarded-User": user });
const post = (path, data, user = "editor") => fetch(base + path, { method: "POST",
    headers: Object.assign({ "Content-Type": "application/json" }, proxy(user)), body: JSON.stringify(data) });
const wsUrl = new URL(base);
wsUrl.protocol = "ws:";
wsUrl.port = String(Number(wsUrl.port || 80) + 1);
const device = new WebSocket(wsUrl.origin + "/ws/device?device=" + dev + "&leds=4");
let streamText = "";
device.onmessage = (msg) => {
    streamText += msg.data + "\n";
};
await new Promise((ok, fail) => {
    device.onopen = ok;
    device.onerror = fail;
});
const report = async (fields) => {
    device.send(JSON.stringify({ event: "report", data: Object.assign({ rssi: -50, settings }, fields) }));
    await sleep(200);
};
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
await report({ options, ack: 0 });
const setTest = (result) => fetch(base + "/web/test/test01", {
    method: "POST", headers: proxy("editor"), body: new URLSearchParams({ result }), redirect: "manual" });
await setTest("PASS");
await post("/api/leds/" + dev, [{ path: ["test", "test01"] }]);

// Chrome tab
const target = await (await fetch("http://127.0.0.1:9333/json/new?about:blank", { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0;
const waiting = {};
const events = [];
ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && waiting[data.id]) {
        waiting[data.id](data.result);
        delete waiting[data.id];
    } else if (data.method) {
        events.push(data);
    }
};
await new Promise((ok) => { ws.onopen = ok; });
const send = (method, params = {}) => new Promise((ok) => {
    id++;
    waiting[id] = ok;
    ws.send(JSON.stringify({ id, method, params }));
});
const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true })).result.value;
const shot = async (name) => {
    if (shots) {
        const res = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
        fs.writeFileSync(shots + "/" + name + ".png", Buffer.from(res.data, "base64"));
    }
};

// Page helpers
const ledSave = () => js("!document.getElementById('save').disabled");
const setSave = () => js("!document.getElementById('settings_save').disabled");
const modal = () => js("document.getElementById('discard').classList.contains('show')");
const header = () => js("document.querySelector('#settings .card-header').innerText.replace(/\\s+/g, ' ')");
const setInput = (selector, value, form) => js(`(() => {
    document.querySelector(${JSON.stringify(selector)}).value = ${JSON.stringify(value)};
    document.getElementById(${JSON.stringify(form)}).dispatchEvent(new Event("input"));
    return 1;
})()`);
let failed = 0;
const check = (text, ok) => {
    console.log((ok ? "ok   " : "FAIL ") + text);
    failed += (ok ? 0 : 1);
};

await send("Page.enable");
await send("Network.enable");
await send("Network.setExtraHTTPHeaders", { headers: proxy("editor") });
await send("Page.navigate", { url: base + "/web/device/" + dev });
await sleep(1500);
check("loaded: both Save buttons disabled", !(await ledSave()) && !(await setSave()));
const activeTab = () => js("document.querySelector('.device-tabs .nav-link.active').innerText");
check("device tabs: this device active", (await activeTab()) === "Test");
await js("document.querySelector('.device-tabs .nav-link').click(); 1");
await sleep(1000);
check("device tabs: Overview shows the table", ((await activeTab()) === "Overview") &&
    (await js("!!document.getElementById('devices')")) && ((await js("location.hash")) === "#/devices"));
await js(`document.querySelector(".device-tabs a[title='${dev}']").click(); 1`);
await sleep(1000);
check("device tabs: back on the device", ((await activeTab()) === "Test") &&
    (await js("!!document.getElementById('ledform')")));
await js(`(() => {
    const select = document.querySelector("select[name=sound_fail_0]");
    select.value = "Bond";
    select.closest(".input-group").querySelector(".sound-test").click();
})()`);
await sleep(500);
check("sound test: stream got the sound event", streamText.includes('"sound":"Bond"'));
check("sound test: status shown", (await js("document.querySelector('#ledform .form-status').innerText"))
    .includes("Sound sent"));
await js(`document.querySelector("select[name=sound_fail_0]").value = "";
    document.querySelector("select[name=sound_fail_0]").dispatchEvent(new Event("change", { bubbles: true }))`);
check("device card: update button disabled without newer firmware",
    await js("document.getElementById('firmware_update').disabled"));
check("settings synced with device", (await header()).includes("synced with device"));
await setInput("input[name=name_1]", "X", "ledform");
check("LED name changed: LED Save enabled", await ledSave());
await setInput("input[name=name_1]", "", "ledform");
check("LED name back: LED Save disabled", !(await ledSave()));

// LED arrangement: click shows the fields of one LED, marks, Revert
const fields = (ix) => `document.querySelector(".led-fields[data-ix='${ix}']")`;
const tile = (ix) => `document.querySelector(".led-tile[data-ix='${ix}']")`;
check("start: fields of LED 1 only", await js(`!${fields(0)}.hidden && ${fields(1)}.hidden && ` +
    `${tile(0)}.classList.contains("selected")`));
await js(`${tile(2)}.click(); 1`);
check("click LED 3: its fields, selected, hash", await js(`!${fields(2)}.hidden && ${fields(0)}.hidden && ` +
    `${tile(2)}.classList.contains("selected") && !${tile(0)}.classList.contains("selected") && ` +
    `location.hash.endsWith("?led=3")`));
await setInput("input[name=name_2]", "Z", "ledform");
check("LED 3 changed: marked, not LED 1", await js(`${tile(2)}.classList.contains("changed") && ` +
    `!${tile(0)}.classList.contains("changed") && !${fields(2)}.querySelector(".led-changed").hidden`));
check("LED 3 changed: Revert enabled", await js("!document.getElementById('cancel').disabled"));
await shot("changed");
await js("document.getElementById('cancel').click(); 1");
await sleep(800);
check("Revert: value back, no mark, buttons disabled", ((await js(
    "document.querySelector('input[name=name_2]').value")) === "") && !(await ledSave()) &&
    (await js(`!${tile(2)}.classList.contains("changed") && document.getElementById('cancel').disabled`)));
await js(`location.hash = location.hash.replace("led=3", "led=2"); 1`);
await sleep(300);
check("hash led=2: fields of LED 2", await js(`!${fields(1)}.hidden && ${fields(2)}.hidden`));

// LED test: Find sends the selected LED, the badge comes with the device report (live)
check("LED test: Find button names LED 2",
    (await js("document.querySelector('[data-led-test=find]').textContent.replace(/\\s+/g, ' ').trim()")) ===
        "Find LED 2");
streamText = "";
await js("document.querySelector('[data-led-test=find]').click(); 1");
await sleep(500);
check("LED test: stream got find LED 2", streamText.includes('"event":"led"') &&
    streamText.includes('{"mode":"find","first":1,"count":1}'));
check("LED test: status shown", (await js("document.querySelector('#ledform .form-status').innerText"))
    .includes("LED test sent: Find LED 2"));
await report({ led_test: { mode: "find", first: 1, count: 1 } });
await sleep(800);
check("LED test: badge from the report, no reload", ((await js(
    "document.querySelector('[data-live=led-test]').innerText")) === "LED test: LED 2") &&
    ((await js("document.querySelector('[data-led-test=find]').textContent.replace(/\\s+/g, ' ').trim()")) ===
        "Find LED 2"));
await shot("ledtest");
await js("document.querySelector('[data-led-test=normal]').click(); 1");
await sleep(500);
check("LED test: Normal sent", streamText.includes('{"mode":"normal"}'));
await report({});
await sleep(800);
check("LED test: badge gone", (await js("document.querySelector('[data-live=led-test]').innerText")) === "");
await js(`${tile(0)}.click(); 1`);

await setInput("input[name=name_1]", "X", "ledform");
await js("document.querySelector('.nav-link').click(); 1");
await sleep(600);
check("navbar link with changes: modal", await modal());
await shot("modal");
await js("document.querySelector('#discard .btn-outline-secondary').click(); 1");
await sleep(600);
check("Cancel: no modal, still on the page", !(await modal()) && (await js("location.hash")).includes(dev));

await setInput("#set_dim_level", "35", "settings");
check("dim level changed: settings Save enabled", await setSave());
await js("document.getElementById('settings_save').click(); 1");
await sleep(600);
check("settings Save with LED changes: no modal", !(await modal()));
await sleep(1500);
check("settings saved, not synced, 1 change pending",
    (await header()).includes("not synced") && (await header()).includes("1 change pending"));
check("LED changes stay", (await js("document.querySelector('input[name=name_1]').value")) === "X");
await setInput("input[name=name_1]", "", "ledform");
check("pending badge on dim level", (await js("document.querySelector('label[for=set_dim_level]').innerText"))
    .includes("pending"));
check("stream got the settings event", streamText.includes('"settings":{"dim_level":35}'));
await shot("pending");

await js("window.liveMarker = 1");
await report({ settings: Object.assign({}, settings, { dim_level: 35 }), ack: 1 });
await sleep(1500);
check("device has it: synced with device, no reload",
    (await header()).includes("synced with device") && !(await header()).includes("not synced") &&
    ((await js("window.liveMarker")) === 1));
check("pending badge gone", !(await js("document.querySelector('label[for=set_dim_level]').innerText"))
    .includes("pending"));

await setTest("FAIL");
await post("/api/leds/" + dev, [{ path: ["test", "test01"], name: "Other" }]);
await sleep(1500);
check("LED config from another browser: new name and state, no reload",
    ((await js("document.querySelector('input[name=name_0]').value")) === "Other") &&
    (await js("document.querySelector('[data-live=tile-0]').innerText")).includes("FAIL") &&
    ((await js("window.liveMarker")) === 1));
check("LED config from another browser: Save disabled", !(await ledSave()));
await setInput("input[name=name_0]", "Mine", "ledform");
await setTest("PASS");
await post("/api/leds/" + dev, [{ path: ["test", "test01"], name: "Other 2" }]);
await sleep(1500);
check("unsaved changes: input kept, state updated", ((await js(
    "document.querySelector('input[name=name_0]').value")) === "Mine") &&
    (await js("document.querySelector('[data-live=tile-0]').innerText")).includes("PASS"));
await setInput("input[name=name_0]", "Other 2", "ledform");

await setInput("input[name=max_age_0]", "soon", "ledform");
await js("document.getElementById('save').click(); 1");
await sleep(1500);
check("bad LED max. age: error shown, Save enabled",
    (await js("document.querySelector('#ledform .form-status').textContent")).length > 0 && (await ledSave()));

await setInput("input[name=name_1]", "Y", "ledform");
await js("document.querySelector('.nav-link').click(); 1");
await sleep(600);
await js("document.getElementById('discard_yes').click(); 1");
await sleep(1500);
check("navbar link, Discard: on the Devices tab", (await js("location.hash")) === "#/devices");
await js("window.liveMarker = 2");
await report({ rssi: -61 });
await sleep(1500);
check("devices table: new signal, no reload", (await js("document.getElementById('devices').innerText"))
    .includes("-61 dBm") && ((await js("window.liveMarker")) === 2));
check("navbar: live badge", (await js("document.getElementById('live').innerText")) === "live");
await js(`(() => {
    const input = document.querySelector(".dt-search input");
    input.value = "0000aa";
    input.dispatchEvent(new Event("input"));
    return 1;
})()`);
await report({ rssi: -62 });
await sleep(1500);
check("devices table: search kept", ((await js("document.querySelector('.dt-search input').value")) === "0000aa") &&
    (await js("document.getElementById('devices').innerText")).includes("-62 dBm"));
await shot("devices");
await js(`(() => {
    const select = document.querySelector(".dt-length select");
    select.value = "100";
    select.dispatchEvent(new Event("change"));
    return 1;
})()`);
await send("Page.navigate", { url: base + "/web/devices" });
await sleep(1500);
check("devices table: page load gives the defaults (200 rows, no search)",
    ((await js("document.querySelector('.dt-length select').value")) === "200") &&
    ((await js("document.querySelector('.dt-search input').value")) === ""));
check("no browser storage", (await js("localStorage.length + sessionStorage.length")) === 0);

// Report of the device: live badge and status badge blink (also the new badge after the live update)
const blinks = async () => {
    await js(`(() => {
        window.blinked = new Set();
        new MutationObserver((list) => list.forEach((rec) => {
            if (rec.target.classList.contains("blink")) {
                window.blinked.add(rec.target.closest("#live") ? "live" : (rec.target.dataset.devStatus || "?"));
            }
        })).observe(document.body, { attributes: true, attributeFilter: ["class"], subtree: true });
        return 1;
    })()`);
    await report({});
    await sleep(1800);
    return js("Array.from(window.blinked).sort().join(',')");
};
let blinked = await blinks();
check("devices table: report blinks live and status badge (" + blinked + ")", blinked === dev + ",live");

await send("Network.setExtraHTTPHeaders", { headers: proxy("guest") });
await send("Page.navigate", { url: base + "/web/device/" + dev });
await sleep(1500);
check("other user: read only badges in LEDs and Settings", await js(`Array.from(document.querySelectorAll(
    ".card-header .badge")).filter((el) => (el.innerText === "read only"))
    .map((el) => el.closest(".card").id).join(",")`) === "ledform,settings");
check("other user: no read only badge in the navbar", !(await js("document.querySelector('nav').innerText"))
    .includes("read only"));
blinked = await blinks();
check("device page: report blinks live and status badge (" + blinked + ")", blinked === dev + ",live");
check("other user: all controls disabled", await js(`Array.from(document.querySelectorAll(
    "#ledform select, #ledform input:not([type=hidden]), #settings select, #settings input, form button"))
    .every((el) => (el.disabled || el.closest("#led_visual")))`));
await js(`${tile(1)}.click(); 1`);
check("other user: click LED 2 shows its fields", await js(`!${fields(1)}.hidden && ${fields(0)}.hidden`));
await shot("readonly");
check("other user: LED config POST refused", (await post("/api/leds/" + dev, [], "guest")).status === 403);

// Device editor of this device (see local-backend.sh)
await send("Network.setExtraHTTPHeaders", { headers: proxy("deveditor") });
await send("Page.navigate", { url: base + "/web/device/" + dev });
await sleep(1500);
check("device editor: no read only badge", !(await js("document.body.innerText")).includes("read only"));
check("device editor: forms enabled", await js(`document.querySelector("main").dataset.canEdit === "true"
    && !document.querySelector("#ledform select").disabled
    && !document.querySelector("#settings input:not([type=hidden])").disabled`));
await send("Network.setExtraHTTPHeaders", { headers: proxy("guest") });
check("no browser dialogs", !events.some((ev) => (ev.method === "Page.javascriptDialogOpening")));

await send("Page.close");
ws.close();
device.close();
console.log(failed ? (failed + " checks failed") : "all checks passed");
process.exit(failed ? 1 : 0);
