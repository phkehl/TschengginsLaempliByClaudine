# CLAUDE.md

Tschenggins Lämpli: devices with WS2801 RGB LED pixels that show CI job states. Types: Chlotz (ESP32 "Cheap
Yellow Display", CYD, ESP32-2432S028), Hello (Wemos D1 mini), Stägge (Mini D1 ESP32, in work), Chlötzli (ESP8266, in
work), Gitta (u-blox NINA-W102 ESP32, 2 MB flash, no OTA), Chischtli (ESP8266, same code as the Chlötzli).
Docs: README.md (overview, Lämplis, dependencies), `laempli/laempli.md` (all Lämplis), `laempli/<device>/<device>.md`
(each Lämpli), `backend/backend.md` (backend). Write "Hello" with a capital H for the Lämpli in text.

## Memory

- Keep the project memory in `MEMORY.md` (this directory, if present: tracked in git, not published). Do not use the
  per-user Claude memory directory.
- Read `MEMORY.md` at the start of a session. Its section "Local setup" has the local facts (USB ports, deploy,
  publish). Update it when the project state, decisions or open tasks change. Remove items that are no longer true.
- Track TODOs and ideas, enumerate them using numbered TODO<n> and IDEA<n> labels

## Git

- Always commit your own changes when a change is done (build OK, tested as far as possible). Do not wait for the
  user to ask. Update `MEMORY.md` in the same commit.
- Commit only your own changes. Files the user added or changed go in a separate commit, or ask.
- Do not push unless the user asks, or as part of a deploy (see `MEMORY.md`)

## Project facts

- Firmware is in `fw/` (sources, `platformio.ini`, `.venv`, `.pio`, `.pio-core`). Run the commands in the repo root.
- Build: `fw/.venv/bin/pio run -d fw`, flash: `fw/.venv/bin/pio run -d fw -t upload`, serial:
  `fw/.venv/bin/pio device monitor -d fw`
- The ttyUSB numbers change after a replug. Use the stable /dev/serial/by-id/ paths (also in `fw/platformio.ini`).
- PlatformIO is in the local `fw/.venv`, its core (toolchains, packages) in `fw/.pio-core` (both in .gitignore)
- All configuration is in `fw/src/<type>/config.hpp` (e.g. `fw/src/chlotz/config.hpp`), not in `fw/platformio.ini`
  build flags
- Hardware docs of the Chlotz (CYD schematic, pinout, specs, enclosure) are in `laempli/chlotz/`
- Backend: `backend/tschenggins-laempli.py` (config `backend/tschenggins-laempli.yaml`, untracked copy of the
  `.example`, code in `backend/laempli/`, device files in `data/`), tests: `python3 -m unittest discover -s tests`.
  Debian packages python3-flask, python3-waitress, python3-websockets, python3-yaml, python3-coloredlogs,
  python3-markdown. Use `/usr/bin/python3`: the shell may have the PlatformIO `fw/.venv` active.
- Private settings are in `secrets.yaml` (git-ignored, format in `secrets.yaml.example`): backend URL and auth, WiFi
  networks by priority (use them to provision the Lämplis), deploy server (host, dir, service for `tools/deploy.py`).
  Do not put these values (host names, URLs, paths, user names) in tracked files.
- Serial shell on the Chlotz (115200 baud, CRLF): `help`, `status`, `config`, `wifi ...`, `backend ...`, `display ...`,
  `ota ...`, `led ...`. Claude can test it with pyserial from `fw/.venv` (reset via RTS).
- Arduino macros clash with names: `DISPLAY`, `SERIAL`, `LINE_MAX` (limits.h). Do not use them as identifiers.
- LEDs connect to CN1: 3.3V (red), GPIO27 data (yellow), GPIO22 clock (green), GND (blue)
- CN1 3.3V comes from AMS1117 U1 (shared with display). U1 total about 300 mA (0.5 W), so LED limit 200 mA.
- Do not use GPIO21 (display backlight) or GPIO35 (input only) for LEDs

## Communication and style

- Use ASD-STE100 Simplified Technical English
- Keep explanations concise and direct; skip conversational filler
- Use active voice. Pick the most common word.
- Use `code` highlighting sparsely
- For section headings, labels, titles use sentence style capitalisation

## Coding standards

- Indent with 4 spaces (also YAML, HTML, JSON, XML, ...)
- Line width is 120 columns (source code, markdown). Up to 160 where it makes sense.
- Comments are <= 7 words, function names <= 4 words, user-facing message strings <= 10 words
- Use `/** ... */` blocks for Doxygen function documentation
- Use C++ style comments (`// ...`), also for multi-line text, not `/* ... */`
- Always use braces `{}`, also for single line if statements
- Do not rely on operator precedence, always use `()` around individual expressions
- Web pages: never use browser storage (`localStorage`, `sessionStorage`, also not through libraries, e.g. DataTables
  `stateSave`). Put only the states that we explicitly want to keep into the URL hash (e.g. search, sort, `?led=3`).
  All other states reset on a page load.
- Yaml: always quote strings

## Tooling

- Always ask before using sudo
- Prefer Debian packages over downloads or vendoring. Ask the user to install them.
- Install toolchains and Python venvs in a local directory and add it to .gitignore
- Ask before reading files outside the project directory
- Prefer YAML config files, prefer JSONC over JSON

## Documentation

- Overview in README.md, usage in the docs files above
- Design details in the source code or in other local .md files
