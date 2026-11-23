# Demo

A demo of the web interface with fake data: for screenshots and screencasts of the docs, and to try the web pages
without real CI or real Lämplis. It changes nothing in the backend code: it starts a normal backend and sends the same
data as GitLab and as the Lämplis.

| File             | Use                                                                                           |
|------------------|-----------------------------------------------------------------------------------------------|
| `demo.py`        | Local backend, fake GitLab (webhook events), 3 fake Lämplis (WebSocket), see `demo.py --help` |
| `demo-media.mjs` | Screenshots (PNG) and animated GIFs of the demo pages in headless Chrome                      |

## Demo backend

```sh
tools/demo/demo.py                       # http://127.0.0.1:8099/web, data in data/demo, Ctrl-C stops
tools/demo/demo.py --pace fast           # 60 times faster, more failures (for screencasts)
```

What the demo does:

- Writes `data/demo/demo.yaml` (127.0.0.1, port 8099, WebSocket server on 8100, editor `demo`) and makes
  `data/demo/data/` new at each start. It refuses a directory with other files but no `demo.yaml`.
- Fake GitLab: 6 fake projects (`acme/firmware`, `acme/web-shop`, `acme/docs`, `acme/ci-images`, `acme/mobile-app`,
  `tools/release`). Pipelines on main, merge request pipelines on feature branches (then merge and branch deletion),
  tag pipelines, a child pipeline (trigger job `image` of `acme/firmware`), a manual deploy job (`acme/web-shop`), job
  failures and retries. Fake users (alice, bob, ...), links to `https://gitlab.example.com`.
- History: 7 days (`--history`) of events before the start, written into the event store. The backend replays them,
  so the Jobs, Plots and source pages have data. More pipelines on work days 8-18 h. `--seed` gives other data.
- 3 fake Lämplis, connected all the time, with their LED configuration:

  | ID               | Name    | LEDs                                                             |
  |------------------|---------|------------------------------------------------------------------|
  | `chlotz-de0001`  | Office  | 12 LEDs: pipelines and jobs of all projects, sounds on 2 LEDs    |
  | `hello-de0002`   | Desk    | 1 LED: `acme/firmware` main                                      |
  | `staegge-de0003` | Hallway | 4 project rows: firmware, web shop, mobile app, CI images        |

  They send reports as the firmware (settings, options, signal, heap) and take the settings from the web page. LED test,
  sound test and firmware update do nothing.

Edit rights: the backend has no login, the reverse proxy sends the user (see [backend](../../backend/backend.md)). For
edits in a browser, send the header `X-Forwarded-User: demo` (e.g. with a browser extension). Without it, the pages
are read only.

## Screenshots and GIFs

Needs Node 20 or newer, Chrome and ffmpeg (Debian package ffmpeg). The PNGs with the normal pace (times as in real CI),
the GIFs with the fast pace (more changes):

```sh
google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/laempli-chrome about:blank &
tools/demo/demo.py &
node --experimental-websocket tools/demo/demo-media.mjs http://127.0.0.1:8099 backend/img png
kill %2
tools/demo/demo.py --pace fast &
sleep 20
node --experimental-websocket tools/demo/demo-media.mjs http://127.0.0.1:8099 backend/img gif
kill %2 %1
```

Output in `backend/img/` (used by [backend](../../backend/backend.md#screenshots) and the README):

- `web-dashboard.png`, `web-devices.png`, `web-device-chlotz.png`, `web-device-hello.png`, `web-device-staegge.png`,
  `web-jobs.png`, `web-plots.png`, `web-gitlab.png`, `web-sources.png`: window 1300 x 800 (device pages: whole page),
  light mode
- `web-dashboard.gif` (dashboard, 20 s, 960 px wide, about 2.5 MB), `web-device.gif` (LEDs of the Chlotz, 20 s)

Look at the images before a commit: the dashboard GIF should show some changes (red tiles, stripes).
