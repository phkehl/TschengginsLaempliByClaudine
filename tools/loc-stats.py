#!/usr/bin/python3
"""Count the lines of code of a commit and put the statistics into README.md

Runs cloc (Debian package cloc) on the files of a commit (default HEAD; uncommitted changes are not counted),
without the vendored libraries (Bootstrap, DataTables). Writes the section "Code statistics" of README.md: the
commit (hash, time, number of commits), a table per language and a table per part (firmware, backend, tests, tools,
docs), each number with its share of the column total, with aligned columns (readable as text). The section is
between the markers <!-- loc-stats begin --> and <!-- loc-stats end -->; without them, it goes before "## License".
Commit README.md after the update (the counted commit is the one before).

Examples:

    tools/loc-stats.py              # count HEAD, update README.md
    tools/loc-stats.py --dry-run    # only print the section
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import textwrap

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(TOP, "README.md")
BEGIN = "<!-- loc-stats begin -->"
END = "<!-- loc-stats end -->"

# Vendored, not counted
EXCLUDE_DIRS = ("bootstrap", "datatables")

# Parts: (name, test of a path); first match wins, Markdown is always "Docs"
PARTS = (
    ("Firmware (`fw/`)", lambda path: path.startswith("fw/")),
    ("Backend (`backend/`)", lambda path: path.startswith("backend/")),
    ("Tests (`tests/`, `tools/test/`)", lambda path: path.startswith(("tests/", "tools/test/"))),
    ("Tools (`tools/`)", lambda path: path.startswith("tools/")),
    ("Other", lambda path: True),
)
DOCS = "Docs (Markdown)"


def run(cmd):
    """Run a command in the repository, returns its output"""
    return subprocess.run(cmd, cwd=TOP, check=True, capture_output=True, text=True).stdout


def count(commit):
    """Run cloc on a commit, returns the per-file entries {path: {"language", "blank", "comment", "code"}}"""
    out = run(["cloc", "--git", commit, "--by-file", "--json", "--quiet", "--exclude-dir=" + ",".join(EXCLUDE_DIRS),
               "--force-lang=YAML,example"])
    data = json.loads(out)
    return {path: entry for path, entry in data.items() if path not in ("header", "SUM")}


def add(table, key, entry):
    """Add the counts of a file to a table row"""
    row = table.setdefault(key, {"files": 0, "code": 0, "comment": 0, "blank": 0})
    row["files"] += 1
    for name in ("code", "comment", "blank"):
        row[name] += entry[name]


def part_of(path, language):
    """Get the part of a file"""
    if language == "Markdown":
        return DOCS
    return next(name for name, test in PARTS if test(path))


def share(value, total):
    """Format a number with its share of the total, e.g. 7,670 (34%)"""
    return "{:,} ({:.0f}%)".format(value, ((100.0 * value / total) if total else 0.0))


def table(title, rows, order):
    """Make a Markdown table, rows in the given order, then the total; columns aligned (readable as text)"""
    names = ("files", "code", "comment", "blank")
    total = {name: sum(rows[key][name] for key in order) for name in names}
    cells = [[title, "Files", "Code", "Comment", "Blank"]]
    cells += [[key] + [share(rows[key][name], total[name]) for name in names] for key in order]
    cells += [["**Total**"] + ["{:,}".format(total[name]) for name in names]]
    widths = [max(len(row[col]) for row in cells) for col in range(len(cells[0]))]
    lines = []
    for ix, row in enumerate(cells):
        text = [row[0].ljust(widths[0])] + [cell.rjust(width) for cell, width in zip(row[1:], widths[1:])]
        lines.append("| " + " | ".join(text) + " |")
        if ix == 0:
            lines.append("|" + "|".join([("-" * (widths[0] + 2))] + [("-" * (width + 1)) + ":" for width in widths[1:]])
                         + "|")
    return lines


def section(commit):
    """Make the README section for a commit"""
    files = count(commit)
    languages = {}
    parts = {}
    for path, entry in files.items():
        path = os.path.normpath(path)
        add(languages, entry["language"], entry)
        add(parts, part_of(path, entry["language"]), entry)
    sha = run(["git", "rev-parse", "--short", commit]).strip()
    stamp = int(run(["git", "show", "-s", "--format=%ct", commit]).strip())
    date = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commits = run(["git", "rev-list", "--count", commit]).strip()
    part_order = [name for name, _ in PARTS if name in parts] + ([DOCS] if DOCS in parts else [])
    intro = ("Commit {} ({}, {} commits), counted with [cloc](https://github.com/AlDanial/cloc). All files in "
             "git, without the vendored Bootstrap and DataTables. Update with `tools/loc-stats.py`.".format(
                 sha, date, commits))
    lines = ([BEGIN, "## Code statistics", ""] +
             textwrap.wrap(intro, 120, break_on_hyphens=False, break_long_words=False) + [""])
    lines += table("Language", languages, sorted(languages, key=lambda key: -languages[key]["code"]))
    lines += [""] + table("Part", parts, part_order)
    lines += [END]
    return "\n".join(lines) + "\n"


def update_readme(text):
    """Put the section into README.md (replace the old one, else before "## License")"""
    with open(README, "r", encoding="utf-8") as fh:
        readme = fh.read()
    if (BEGIN in readme) and (END in readme):
        start = readme.index(BEGIN)
        end = readme.index(END) + len(END) + 1
        readme = readme[:start] + text + readme[end:]
    elif "\n## License" in readme:
        pos = readme.index("\n## License") + 1
        readme = readme[:pos] + text + "\n" + readme[pos:]
    else:
        readme = readme.rstrip("\n") + "\n\n" + text
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(readme)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0], epilog=__doc__.split("\n", 1)[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", default="HEAD", help="commit to count (default HEAD)")
    parser.add_argument("--dry-run", action="store_true", help="only print the section")
    args = parser.parse_args(argv)
    if shutil.which("cloc") is None:
        sys.exit("cloc missing, please run: sudo apt install cloc")
    text = section(args.commit)
    if args.dry_run:
        print(text, end="")
    else:
        update_readme(text)
        print("README.md: code statistics of {}".format(args.commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
