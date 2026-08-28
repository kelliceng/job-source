"""Tier 0: ask each ATS directly whether a company account exists.

Every major ATS publishes its customers' open jobs at a predictable address,
readable with no login and no API key. So we don't have to *guess* which ATS a
company uses -- we ask all of them, in parallel, and the one that answers with
real jobs is the answer. A wrong guess returns 404 or an empty list.

That 200-with-jobs response is also our proof: we're not inferring that Harvey
uses Ashby, Ashby told us so and handed back 351 postings.
"""
import asyncio
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


@dataclass
class Provider:
    id: str
    label: str
    api: Callable[[str], str]
    board: Callable[[str], str]
    parse: Callable[[object], Optional[List[Tuple[str, str]]]]


def _list_of(data, key, title_key, url_key):
    """Pull [(title, url), ...] out of a provider's JSON, or None if shape is wrong."""
    rows = data.get(key) if isinstance(data, dict) else (data if key is None else None)
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get(title_key):
            out.append((str(r[title_key]), str(r.get(url_key) or "")))
    return out


PROVIDERS: List[Provider] = [
    Provider(
        "greenhouse", "Greenhouse",
        lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        lambda s: f"https://job-boards.greenhouse.io/{s}",
        lambda d: _list_of(d, "jobs", "title", "absolute_url"),
    ),
    Provider(
        "lever", "Lever",
        lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json",
        lambda s: f"https://jobs.lever.co/{s}",
        lambda d: _list_of(d, None, "text", "hostedUrl"),
    ),
    Provider(
        "ashby", "Ashby",
        lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{s}",
        lambda s: f"https://jobs.ashbyhq.com/{s}",
        lambda d: _list_of(d, "jobs", "title", "jobUrl"),
    ),
    Provider(
        "smartrecruiters", "SmartRecruiters",
        lambda s: f"https://api.smartrecruiters.com/v1/companies/{s}/postings",
        lambda s: f"https://careers.smartrecruiters.com/{s}",
        lambda d: _list_of(d, "content", "name", "ref"),
    ),
    Provider(
        "workable", "Workable",
        lambda s: f"https://apply.workable.com/api/v1/widget/accounts/{s}?details=true",
        lambda s: f"https://apply.workable.com/{s}",
        lambda d: _list_of(d, "jobs", "title", "url"),
    ),
    Provider(
        "recruitee", "Recruitee",
        lambda s: f"https://{s}.recruitee.com/api/offers/",
        lambda s: f"https://{s}.recruitee.com",
        lambda d: _list_of(d, "offers", "title", "careers_url"),
    ),
    Provider(
        "breezy", "Breezy HR",
        lambda s: f"https://{s}.breezy.hr/json",
        lambda s: f"https://{s}.breezy.hr",
        lambda d: _list_of(d, None, "name", "url"),
    ),
]

PROVIDERS_BY_ID = {p.id: p for p in PROVIDERS}


@dataclass
class Hit:
    provider: Provider
    slug: str
    board_url: str
    jobs: List[Tuple[str, str]]
    strong: bool = True     # was this slug a confident guess, or a truncation?

    @property
    def count(self) -> int:
        return len(self.jobs)


async def _probe(client: httpx.AsyncClient, provider: Provider, slug: str,
                 sem: asyncio.Semaphore, strong: bool = True) -> Optional[Hit]:
    """One (provider, slug) guess. Returns a Hit only if real jobs come back."""
    url = provider.api(slug)
    try:
        async with sem:
            r = await client.get(url)
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None

    if r.status_code != 200:
        return None
    # Some providers serve an HTML error page with a 200; that's not a match.
    if "json" not in r.headers.get("content-type", "").lower():
        return None
    try:
        data = r.json()
    except Exception:
        return None

    jobs = provider.parse(data)
    # An account that exists but has zero open roles is not a useful answer,
    # and is indistinguishable from a wrong guess. Require at least one job.
    if not jobs:
        return None
    return Hit(provider=provider, slug=slug, board_url=provider.board(slug),
               jobs=jobs, strong=strong)


async def probe_all(slugs: List[str], providers: Optional[List[Provider]] = None,
                    concurrency: int = 12, timeout: float = 12.0) -> List[Hit]:
    """Try slugs against providers in waves: slug 1 against everything, then
    slug 2, and so on. Most companies match on the first slug, so we usually
    make 7 requests and stop -- not 56."""
    providers = providers or PROVIDERS
    sem = asyncio.Semaphore(concurrency)
    hits: List[Hit] = []
    # Accept plain strings or SlugCandidate objects.
    pairs = [(str(s), getattr(s, "strong", True)) for s in slugs]

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": UA}) as client:
        for slug, strong in pairs:
            results = await asyncio.gather(
                *[_probe(client, p, slug, sem, strong) for p in providers]
            )
            wave = [h for h in results if h]
            if wave:
                hits.extend(wave)
                break   # first slug that matches anywhere wins
    # Most jobs first -- if two providers both answer, the fuller board is
    # almost always the live one (companies leave stale empty boards behind).
    return sorted(hits, key=lambda h: h.count, reverse=True)


def probe_all_sync(slugs: List[str], **kw) -> List[Hit]:
    """Blocking wrapper around probe_all.

    Playwright's sync API runs its own asyncio loop on the calling thread, so
    a bare asyncio.run() here raises "cannot be called from a running event
    loop" whenever Tier 2 has a browser open. When a loop is already running,
    do the work on a separate thread with its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(probe_all(slugs, **kw))

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(probe_all(slugs, **kw))).result()
