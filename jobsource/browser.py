"""Tier 2, part 1: render a page in a real browser and describe its links.

Tier 1 fails on sites that build themselves with JavaScript -- the downloaded
HTML is a near-empty shell. Harvey's careers page is 333KB of JavaScript in
which the string "ashby" appears zero times; the link only exists after the
page runs. So we run it.

What we hand to the model is deliberately NOT the raw HTML. A rendered page is
several hundred KB, almost all styling and scripts. We extract a compact
inventory -- link text, destination, and where on the page it sits -- which is
~40 lines, costs a fraction of a cent, and gives better answers than burying
the question in markup.
"""
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse

# Collect anchors along with their structural context. Running this inside the
# page is far cheaper than shipping the DOM out and parsing it here.
_EXTRACT_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!href || href.startsWith('#') || href.startsWith('mailto:')
        || href.startsWith('tel:') || href.startsWith('javascript:')) continue;

    const text = (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' ');
    const label = a.getAttribute('aria-label') || a.getAttribute('title') || '';
    if (!text && !label) continue;

    // Where does this link live? Nav and footer links are navigation;
    // links in the body are usually content.
    let zone = 'body';
    for (let el = a; el; el = el.parentElement) {
      const tag = (el.tagName || '').toLowerCase();
      const role = (el.getAttribute && el.getAttribute('role')) || '';
      if (tag === 'footer' || role === 'contentinfo') { zone = 'footer'; break; }
      if (tag === 'nav' || tag === 'header' || role === 'navigation') { zone = 'nav'; break; }
    }

    const key = a.href + '|' + text.slice(0, 40);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ text: text.slice(0, 90), label: label.slice(0, 60),
               href: a.href, zone });
  }
  return out;
}
"""


@dataclass
class Link:
    text: str
    href: str
    zone: str          # nav | footer | body
    label: str = ""

    def describe(self, base: str = "") -> str:
        """One compact line for the model. Same-origin URLs lose their host."""
        href = self.href
        if base:
            b = urlparse(base)
            p = urlparse(href)
            if p.netloc == b.netloc:
                href = p.path + (f"?{p.query}" if p.query else "") or "/"
        words = self.text or self.label
        return f"[{self.zone}] {words} -> {href}"


@dataclass
class Page:
    url: str
    title: str
    links: List[Link]
    text_sample: str


class Browser:
    """Thin wrapper over Playwright. Use as a context manager so the browser
    is launched once and reused across hops instead of per page."""

    def __init__(self, timeout: float = 20.0, headless: bool = True):
        self.timeout = timeout
        self.headless = headless
        self._pw = None
        self._browser = None

    # Free hosting tiers are memory-constrained, and Chromium is the thing
    # that gets OOM-killed. These flags cut its footprint substantially with
    # no effect on which links a page contains.
    LEAN_ARGS = [
        "--disable-dev-shm-usage",      # /dev/shm is tiny in containers
        "--no-zygote",                  # skip the fork-server process
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=TranslateUI,BackForwardCache",
        "--no-sandbox",                 # required in most container runtimes
        "--mute-audio",
    ]

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless, args=self.LEAN_ARGS)
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def visit(self, url: str) -> Optional[Page]:
        ctx = self._browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        # Images and fonts don't affect which links exist -- skip them. Roughly
        # halves page load time across a 20-URL run.
        page.route("**/*", lambda route: (
            route.abort() if route.request.resource_type in
            ("image", "media", "font") else route.continue_()
        ))
        try:
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
            # Give client-side rendering a moment to populate the DOM.
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            raw = page.evaluate(_EXTRACT_JS)
            body = page.evaluate(
                "() => (document.body ? document.body.innerText : '').slice(0, 1200)")
            return Page(
                url=page.url,
                title=page.title() or "",
                links=[Link(text=str(r.get("text") or ""),
                            href=str(r.get("href") or ""),
                            zone=str(r.get("zone") or "body"),
                            label=str(r.get("label") or ""))
                       for r in raw if r.get("href")],
                text_sample=" ".join((body or "").split())[:1200],
            )
        except Exception:
            return None
        finally:
            ctx.close()
