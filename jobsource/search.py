"""Tier 3: find the board via a search API.

The last resort, and the only route to Workday. Workday addresses look like
  acme.wd3.myworkdayjobs.com/en-US/Careers
where the tenant and the `wd3` shard are assigned by Workday, so Tier 0 can
never guess them -- but they are indexed.

Why an API and not the browser we already have: search engines actively block
automated querying (DuckDuckGo answers 202, Bing degrades results), and
engineering around that is bot-detection evasion, which this project doesn't
do. A search API is the supported interface for exactly this, and Brave's free
tier covers far more than this project needs.

  Free key: https://brave.com/search/api/  ->  BRAVE_API_KEY
"""
import os
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .site import WORKDAY, find_ats, normalize_board_url

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# Domains that are never the company's own board, however well they rank.
_AGGREGATORS = re.compile(
    r"(linkedin|indeed|glassdoor|ziprecruiter|monster|simplyhired|jooble|"
    r"talent\.com|neuvoo|careerjet|jobrapido|adzuna|dice\.com|wellfound|"
    r"builtin|levels\.fyi|reddit|facebook|twitter|x\.com|youtube|wikipedia)",
    re.I)


def available() -> bool:
    return bool(os.environ.get("BRAVE_API_KEY"))


def search(query: str, count: int = 15,
           client: Optional[httpx.Client] = None) -> List[str]:
    """One search. Returns result URLs, aggregators removed, order preserved."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return []
    owns = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        r = client.get(BRAVE_ENDPOINT,
                       params={"q": query, "count": count},
                       headers={"Accept": "application/json",
                                "X-Subscription-Token": key})
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        if owns:
            client.close()

    out = []
    for item in (data.get("web", {}) or {}).get("results", []) or []:
        u = item.get("url") or ""
        host = urlparse(u).netloc.lower()
        if u and host and not _AGGREGATORS.search(host):
            out.append(u)
    return out


def find_board(company: str,
               website: Optional[str] = None) -> Optional[Tuple]:
    """Search for a company's job board.

    Returns (provider, slug, url) or None. The ATS-targeted query runs first,
    because a hit there is verifiable against a public API -- unlike a generic
    careers page, which we can only assert.
    """
    if not available():
        return None

    queries = [f'"{company}" careers greenhouse lever ashby myworkdayjobs']
    if website:
        host = urlparse(website).netloc.replace("www.", "")
        if host:
            queries.append(f'site:{host} careers jobs')
    queries.append(f'"{company}" careers open positions')

    with httpx.Client(timeout=15.0) as client:
        for q in queries:
            urls = search(q, client=client)
            if not urls:
                continue

            # Workday first -- the case Tier 0 structurally cannot solve.
            for u in urls:
                if WORKDAY.search(u):
                    return ("workday", None, normalize_board_url(u))

            # Then any other recognisable ATS.
            for u in urls:
                refs = find_ats(u)
                if refs:
                    r = refs[0]
                    return (r.provider, r.slug, normalize_board_url(r.url))

            # Then a careers page on a plausible domain.
            for u in urls:
                p = urlparse(u)
                if re.search(r"(career|job|vacanc|stellen|karriere)",
                             p.netloc + p.path, re.I):
                    return (None, None, u.split("#")[0].rstrip("/"))
    return None
