"""Docs tab: the Markdown docs of the repository as HTML

The backend renders the files in DOCS once at start (Debian package python3-markdown; without it the Docs tab shows a
note). Heading IDs as on GitHub, so the section links of the docs work. Links in the HTML:

- Link to a file in DOCS (relative path, optional #section): the Docs tab of that file, the section in the hash
  (?sec=<id>, see laempli.js)
- #section: the same, for this file
- Other relative links (files not in DOCS): plain text
- http(s): opens in a new browser tab
- Images next to the docs (png, jpg, gif, svg): served by /web/docs/file/<path>, only the ones the docs use
"""

import html
import logging
import os
import posixpath
import re
import urllib.parse

try:
    import markdown
except ImportError:  # pragma: no cover
    markdown = None

LOG = logging.getLogger(__name__)

# Docs: (name, path from the repository top, tab title)
DOCS = (
    ("readme", "README.md", "Overview"),
    ("laempli", "laempli/laempli.md", "Lämplis"),
    ("chloetzli", "laempli/chloetzli/chloetzli.md", "Chlötzli"),
    ("chischtli", "laempli/chischtli/chischtli.md", "Chischtli"),
    ("gitta", "laempli/gitta/gitta.md", "Gitta"),
    ("hello", "laempli/hello/hello.md", "Hello"),
    ("chlotz", "laempli/chlotz/chlotz.md", "Chlotz"),
    ("staegge", "laempli/staegge/staegge.md", "Stägge"),
    ("backend", "backend/backend.md", "Backend"),
)

# Image files the docs may show
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".svg")

# Repository top: backend/laempli/docs.py -> ../..
TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Placeholder for the URL path of the backend (proxy prefix), see html()
ROOT = "@@ROOT@@"


def github_slug(text, separator="-"):
    """Get heading ID as GitHub: lower case, no punctuation, spaces to "-" (not joined)"""
    text = re.sub(r"<[^>]*>", "", html.unescape(text)).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", separator)


class Docs:
    """Rendered docs: name -> {"title", "path", "html"}, or an error text"""

    def __init__(self, top=TOP, docs=DOCS):
        """Render the docs: top: repository top, docs: list of (name, path, title)"""
        self.top = top
        self.docs = {}
        self.order = [name for name, _, _ in docs]
        self.files = set()
        self.error = None
        self.by_path = {path: name for name, path, _ in docs}
        if markdown is None:
            self.error = "Package python3-markdown not installed"
            LOG.warning("Docs: %s", self.error)
        for name, path, title in docs:
            self.docs[name] = {"title": title, "path": path, "html": self._render(name, path)}
        LOG.info("Docs: %d files, %d images", len(self.docs), len(self.files))

    def _render(self, name, path):
        # HTML of one doc, None if not possible
        if markdown is None:
            return None
        try:
            with open(os.path.join(self.top, path), "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as ex:
            LOG.warning("Docs: %s: %s", path, ex)
            return None
        md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc"],
                               extension_configs={"toc": {"slugify": github_slug}})
        body = md.convert(text)
        body = re.sub(r'<a href="([^"]*)"', lambda m: self._link(name, path, m.group(1)), body)
        body = re.sub(r'<img ([^>]*)src="([^"]*)"', lambda m: self._image(path, m.group(1), m.group(2)), body)
        return body

    def _resolve(self, path, target):
        # Path of a relative link from the repository top, None if outside
        full = posixpath.normpath(posixpath.join(posixpath.dirname(path), urllib.parse.unquote(target)))
        return (None if (full.startswith("../") or full.startswith("/")) else full)

    def _link(self, name, path, href):
        # Link of the docs: docs tab, new browser tab or plain text
        url = html.unescape(href)
        if re.match(r"^[a-z]+:", url):
            return '<a target="_blank" rel="noopener" href="{}"'.format(href)
        target, _, sec = url.partition("#")
        doc = (name if (not target) else self.by_path.get(self._resolve(path, target)))
        if doc is None:
            return '<a class="doc-nolink" title="Not in the docs"'
        return '<a href="{}"'.format(html.escape(ROOT + "/web#/docs/" + doc + (("?sec=" + sec) if sec else "")))

    def _image(self, path, attrs, src):
        # Image next to the docs: served URL, else as it is
        target = self._resolve(path, html.unescape(src))
        if target and target.lower().endswith(IMAGE_TYPES) and os.path.isfile(os.path.join(self.top, target)):
            self.files.add(target)
            src = html.escape(ROOT + "/web/docs/file/" + urllib.parse.quote(target))
        return '<img {}src="{}"'.format(attrs, src)

    def tabs(self):
        """Get (name, title) of the docs, in order"""
        return [(name, self.docs[name]["title"]) for name in self.order]

    def get(self, name):
        """Get a doc (dict with title, path, html; html None if not rendered), None if unknown"""
        return self.docs.get(name)

    def html(self, name, root):
        """Get HTML of a doc with the URL path of the backend (root, e.g. "/laempli"), None if not rendered"""
        body = self.docs[name]["html"]
        return (None if (body is None) else body.replace(ROOT, root))

    def file_path(self, path):
        """Get full path of an image of the docs, None if the docs do not use it"""
        return (os.path.join(self.top, path) if (path in self.files) else None)
