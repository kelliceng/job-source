"""Tier 1: read the company's own website and follow it to their job board.

Plain HTTP, no browser. We download the homepage as text, look for a careers
link, follow it, and then look for either a link out to a known ATS or an
embedded ATS widget (which carries the account name right in the markup).

This costs nothing and takes ~1-3s. It fails on sites that build themselves
with JavaScript, which is what Tier 2 is for.
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Wording that means "careers", across the phrasings companies actually use.
CAREER_TEXT = re.compile(
    r"\b(careers?|jobs?|join\s+(us|our|the)|open\s+(roles|positions)|"
    r"we[''`]?re\s+hiring|work\s+(with|at|for)\s+us|opportunities|vacancies|"
    r"employment|life\s+at|recrut|carri[eè]re|stellen|empleo|lavora)\b", re.I)

CAREER_HREF = re.compile(
    r"(careers?|jobs?|join-?us|open-?roles|hiring|vacanc|recruit|stellenangebote)", re.I)

# Paths worth trying blind if no link is found.
WELL_KNOWN = ["/careers", "/careers/", "/jobs", "/jobs/", "/company/careers",
              "/about/careers", "/join-us", "/work-with-us", "/en/careers",
              "/careers/jobs", "/about/jobs"]

# How to recognise an ATS from a URL, and pull the account name out of it.
ATS_URL_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9\-_]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board\?for=([a-z0-9\-_]+)", re.I)),
    ("lever",      re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9\-_]+)", re.I)),
    ("ashby",      re.compile(r"jobs\.ashbyhq\.com/([a-z0-9\-_]+)", re.I)),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([a-z0-9\-_]+)", re.I)),
    ("workable",   re.compile(r"apply\.workable\.com/([a-z0-9\-_]+)", re.I)),
    ("recruitee",  re.compile(r"([a-z0-9\-_]+)\.recruitee\.com", re.I)),
    ("breezy",     re.compile(r"([a-z0-9\-_]+)\.breezy\.hr", re.I)),
    ("teamtailor", re.compile(r"([a-z0-9\-_]+)\.teamtailor\.com", re.I)),
    ("personio",   re.compile(r"([a-z0-9\-_]+)\.jobs\.personio\.(?:de|com)", re.I)),
]

# Workday is special: the account name is unguessable, so finding the URL in
# the wild is the *only* way to get it. We keep the whole URL, not a slug.
WORKDAY = re.compile(r"https?://[a-z0-9\-]+\.wd\d+\.myworkdayjobs\.com/[^\s\"'<>]+", re.I)

_NOT_A_SLUG = {"embed", "job_board", "www", "api", "static", "assets"}


def normalize_board_url(url: str) -> str:
    """Trim an individual job-detail URL back to the board that lists them all.

    The brief asks for the *listing* page, but the link we find in the wild is
    often a single posting -- e.g. Workday's
      .../UMGAPAC/job/Singapore-Singapore/Creative-Intern_UMG-17166
    whose listing page is just
      .../UMGAPAC
    """
    # Workday: everything before /job/ or /details/ is the board.
    m = re.match(r"(https?://[a-z0-9\-]+\.wd\d+\.myworkdayjobs\.com/"
                 r"(?:[a-zA-Z\-]{2,5}/)?[^/]+?)"
                 r"(?:/(?:job|details|login|home|apply)\b.*)?$", url, re.I)
    if m:
        return m.group(1)
    # Greenhouse / Lever / Ashby detail pages: board is one path segment deep.
    m = re.match(r"(https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/[^/]+)"
                 r"(?:/jobs/\d+)?", url, re.I)
    if m:
        return m.group(1)
    m = re.match(r"(https?://jobs\.(?:eu\.)?lever\.co/[^/]+)(?:/[0-9a-f\-]{8,}.*)?$",
                 url, re.I)
    if m:
        return m.group(1)
    m = re.match(r"(https?://jobs\.ashbyhq\.com/[^/]+)(?:/[0-9a-f\-]{8,}.*)?$",
                 url, re.I)
    if m:
        return m.group(1)
    return url


BOARD_URL = {
    "greenhouse":      lambda s: f"https://job-boards.greenhouse.io/{s}",
    "lever":           lambda s: f"https://jobs.lever.co/{s}",
    "ashby":           lambda s: f"https://jobs.ashbyhq.com/{s}",
    "smartrecruiters": lambda s: f"https://careers.smartrecruiters.com/{s}",
    "workable":        lambda s: f"https://apply.workable.com/{s}",
    "recruitee":       lambda s: f"https://{s}.recruitee.com",
    "breezy":          lambda s: f"https://{s}.breezy.hr",
    "teamtailor":      lambda s: f"https://{s}.teamtailor.com/jobs",
    "personio":        lambda s: f"https://{s}.jobs.personio.de",
}


@dataclass
class AtsRef:
    provider: str
    slug: Optional[str]
    url: str


def find_ats(html: str, base_url: str = "") -> List[AtsRef]:
    """Find every ATS reference in a page -- links, iframes, and embed scripts."""
    found: List[AtsRef] = []
    seen = set()

    for m in WORKDAY.finditer(html):
        url = normalize_board_url(m.group(0).rstrip("\"'&;,"))
        if url not in seen:
            seen.add(url)
            found.append(AtsRef("workday", None, url))

    for provider, pat in ATS_URL_PATTERNS:
        for m in pat.finditer(html):
            slug = m.group(1).lower()
            if slug in _NOT_A_SLUG or len(slug) < 2:
                continue
            key = (provider, slug)
            if key in seen:
                continue
            seen.add(key)
            builder = BOARD_URL.get(provider)
            url = builder(slug) if builder else urljoin(base_url, m.group(0))
            found.append(AtsRef(provider, slug, url))
    return found


def career_links(html: str, base_url: str) -> List[str]:
    """Candidate careers URLs on a page, best guesses first.

    Footer links score highest -- that's where careers almost always lives.
    """
    tree = HTMLParser(html)
    host = urlparse(base_url).netloc.lower()
    scored = []

    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        text = " ".join((node.text() or "").split())[:80]
        label = f"{text} {node.attributes.get('aria-label','')} {node.attributes.get('title','')}"

        score = 0
        if CAREER_TEXT.search(label):
            score += 3
        if CAREER_HREF.search(href):
            score += 3
        if not score:
            continue

        url = urljoin(base_url, href)
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            continue

        # A link straight to a known ATS is the best possible outcome.
        if find_ats(url):
            score += 6
        # Prefer staying on the company's own domain over random third parties.
        elif p.netloc.lower() and host and p.netloc.lower() != host:
            if not p.netloc.lower().endswith(host.replace("www.", "")):
                score -= 2
        # Short paths beat deep blog-post URLs that merely mention hiring.
        score -= min(len([s for s in p.path.split("/") if s]), 4) * 0.3
        scored.append((score, url))

    scored.sort(key=lambda t: -t[0])
    out, seen = [], set()
    for _, u in scored:
        u = u.split("#")[0].rstrip("/")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def get(client: httpx.Client, url: str) -> Optional[httpx.Response]:
    try:
        r = client.get(url)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "").lower():
            return r
    except httpx.HTTPError:
        pass
    return None


def crawl(website: str, max_pages: int = 4,
          client: Optional[httpx.Client] = None) -> Tuple[List[AtsRef], List[str]]:
    """Walk from a company homepage toward their job board.

    Returns (ats references found, pages visited).
    """
    owns = client is None
    client = client or httpx.Client(timeout=12.0, follow_redirects=True,
                                    headers={"User-Agent": UA})
    visited: List[str] = []
    try:
        home = get(client, website)
        if home is None:
            return [], visited
        visited.append(str(home.url))
        base = str(home.url)

        # The homepage itself sometimes links straight to the board.
        refs = find_ats(home.text, base)
        if refs:
            return refs, visited

        targets = career_links(home.text, base)
        # If nothing looked like a careers link, try the usual paths blind.
        if not targets:
            root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
            targets = [root + p for p in WELL_KNOWN]

        for url in targets[:max_pages]:
            page = get(client, url)
            if page is None:
                continue
            visited.append(str(page.url))
            # The URL we landed on may itself be the ATS (after redirects).
            refs = find_ats(str(page.url) + " " + page.text, str(page.url))
            if refs:
                return refs, visited
        return [], visited
    finally:
        if owns:
            client.close()
