// Screenshots and animated GIFs of the demo web pages for the docs (Chrome DevTools protocol)
//
// Usage: node --experimental-websocket tools/demo/demo-media.mjs [<base URL>] [<out dir>] [<what>]
//
// Needs the demo (tools/demo/demo.py, default base URL http://127.0.0.1:8099, best with --pace fast for the GIFs),
// headless Chrome with remote debugging on port 9333 and ffmpeg (Debian package ffmpeg):
//
//     google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/laempli-chrome about:blank &
//
// Writes into <out dir> (default backend/img): web-<page>.png (dashboard, devices, the device page of each fake
// Lämpli, jobs, plots, GitLab events, sources; window 1300 x 800, device pages: full page), web-dashboard.gif and
// web-device.gif (the LEDs of the Chlotz; frames for GIF_SECONDS, at most GIF_WIDTH wide). <what>: "png", "gif" or
// "all" (default). Sends the X-Forwarded-User header of the demo editor. Only for a local demo: it refuses other hosts
// than 127.0.0.1 and localhost.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const top = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");
const base = (process.argv[2] || "http://127.0.0.1:8099").replace(/\/$/, "");
const outDir = process.argv[3] || path.join(top, "backend", "img");
const what = process.argv[4] || "all";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) {
    console.error("Only for a local demo: " + base);
    process.exit(2);
}
const WIDTH = 1300;
const HEIGHT = 800;
const GIF_SECONDS = 20;
const GIF_FPS = 8;
const GIF_WIDTH = 960;
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

// Chrome tab
const target = await (await fetch("http://127.0.0.1:9333/json/new?about:blank", { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0;
const waiting = {};
ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && waiting[data.id]) {
        waiting[data.id](data.result);
        delete waiting[data.id];
    }
};
await new Promise((ok) => { ws.onopen = ok; });
const send = (method, params = {}) => new Promise((ok) => {
    id++;
    waiting[id] = ok;
    ws.send(JSON.stringify({ id, method, params }));
});
const js = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true })).result.value;
const capture = async (full, clip) => Buffer.from((await send("Page.captureScreenshot",
    Object.assign({ format: "png", captureBeyondViewport: !!full }, clip ? { clip } : {}))).data, "base64");
const open = async (page, wait = 2500) => {
    await send("Page.navigate", { url: base + page });
    await sleep(wait);
};

await send("Page.enable");
await send("Network.enable");
await send("Network.setExtraHTTPHeaders", { headers: { "X-Forwarded-User": "demo" } });
await send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
fs.mkdirSync(outDir, { recursive: true });

// Screenshots
const PAGES = [
    ["dashboard", "/web/dashboard", false],
    ["devices", "/web/devices", false],
    ["device-chlotz", "/web/device/chlotz-de0001", true],
    ["device-hello", "/web/device/hello-de0002", true],
    ["device-staegge", "/web/device/staegge-de0003", true],
    ["jobs", "/web/jobs", false],
    ["plots", "/web/plots", false],
    ["gitlab", "/web/gitlab", false],
    ["sources", "/web/sources", false],
];
if (["png", "all"].includes(what)) {
    for (const [name, page, full] of PAGES) {
        await open(page);
        const file = path.join(outDir, "web-" + name + ".png");
        fs.writeFileSync(file, await capture(full));
        console.log("ok   " + file);
    }
}

// GIF: frames with their times, ffmpeg concat (frame durations), one palette. selector: only this element.
const gif = async (name, page, selector) => {
    await open(page, 3000);
    let clip = null;
    if (selector) {
        clip = await js(`(() => {
            const box = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();
            return { x: box.x, y: box.y + scrollY, width: box.width, height: box.height, scale: 1 };
        })()`);
    }
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "laempli-gif-"));
    const frames = [];
    const start = Date.now();
    while ((Date.now() - start) < (GIF_SECONDS * 1000)) {
        const time = Date.now();
        const file = path.join(dir, String(frames.length).padStart(4, "0") + ".png");
        fs.writeFileSync(file, await capture(!!clip, clip));
        frames.push([file, time]);
        await sleep(Math.max(0, (1000 / GIF_FPS) - (Date.now() - time)));
    }
    const list = frames.map(([file, time], ix) => "file '" + file + "'\nduration " +
        (((ix + 1 < frames.length) ? frames[ix + 1][1] : (time + (1000 / GIF_FPS))) - time) / 1000).join("\n");
    fs.writeFileSync(path.join(dir, "list.txt"), list + "\nfile '" + frames[frames.length - 1][0] + "'\n");
    const out = path.join(outDir, "web-" + name + ".gif");
    const res = spawnSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
        path.join(dir, "list.txt"), "-vf", "fps=" + GIF_FPS + ",scale='min(" + GIF_WIDTH + ",iw)':-1:flags=lanczos," +
        "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:" +
        "diff_mode=rectangle", "-loop", "0", out], { stdio: "inherit" });
    fs.rmSync(dir, { recursive: true });
    if (res.status !== 0) {
        console.error("FAIL ffmpeg for " + out);
        process.exitCode = 1;
        return;
    }
    console.log("ok   " + out + " (" + frames.length + " frames, " + Math.round(fs.statSync(out).size / 1024) +
        " kB)");
};
if (["gif", "all"].includes(what)) {
    await gif("dashboard", "/web/dashboard");
    await gif("device", "/web/device/chlotz-de0001", "#ledform .card-body");
}

await fetch("http://127.0.0.1:9333/json/close/" + target.id);
ws.close();
