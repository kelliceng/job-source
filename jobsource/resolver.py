"""The ladder. Try each tier in order, stop at the first confident answer."""
from typing import Optional

from . import agent as agent_mod
from . import ats as ats_mod
from . import cache
from . import linkedin as li
from . import site as site_mod
from . import slugs as slug_mod
from .models import Evidence, LinkedInJob, Resolution
from .verify import score, title_match


def _job_cached(url: str) -> LinkedInJob:
    hit = cache.get(f"job:{url}")
    if hit:
        return LinkedInJob(**hit)
    job = li.fetch_job(url)
    cache.put(f"job:{url}", job.__dict__)
    return job


def _website_cached(slug: str) -> Optional[str]:
    hit = cache.get(f"web:{slug}")
    if hit is not None:
        return hit.get("url")
    web = li.fetch_company_website(slug)
    cache.put(f"web:{slug}", {"url": web})
    return web


def resolve(linkedin_url: str, verbose: bool = False, use_tier2: bool = True,
            picker=None, browser=None) -> Resolution:
    res = Resolution(linkedin_url=linkedin_url)

    def say(msg: str) -> None:
        res.log(msg)
        if verbose:
            print(f"  {msg}")

    # ---- Step 1: read the LinkedIn posting (no login needed) -------------
    try:
        job = _job_cached(linkedin_url)
    except li.RateLimited as e:
        res.error = "linkedin rate limited"
        say(f"FAIL  {e}")
        return res
    except ValueError as e:
        res.error = f"linkedin unreadable: {e}"
        say(f"FAIL  LinkedIn: {e}")
        return res

    res.company_name = job.company_name
    say(f"LinkedIn -> {job.company_name!r}"
        + (f" | {job.job_title!r}" if job.job_title else ""))

    # ---- Tier 0: guess the ATS account name, then check it ---------------
    cands = slug_mod.candidates(job.company_name, job.company_slug)
    say(f"TIER 0  slugs {[(c.slug, 'strong' if c.strong else 'weak') for c in cands]}")
    hits = ats_mod.probe_all_sync(cands)

    for hit in hits:
        matched = title_match(job.job_title, hit.jobs)
        # A truncated slug ("universal" from "Universal Music Group UK") can
        # land on a completely unrelated company that grabbed that word. We
        # only believe a weak slug if the job we came from is on the board.
        if not hit.strong and not matched:
            say(f"TIER 0  rejected weak slug '{hit.slug}' on "
                f"{hit.provider.label} ({hit.count} jobs) -- no title match")
            continue

        res.board_url = hit.board_url
        res.provider = hit.provider.id
        res.slug = hit.slug
        res.job_count = hit.count
        res.tier = 0
        res.confidence = score(True, matched, hit.count)
        res.evidence.append(Evidence(
            "ats_api",
            f"{hit.provider.label} returned {hit.count} open jobs for '{hit.slug}'"))
        if matched:
            res.evidence.append(Evidence(
                "title_match", f"our LinkedIn job appears on the board as {matched!r}"))
        say(f"TIER 0  HIT {hit.provider.label} '{hit.slug}' "
            f"({hit.count} jobs){' + title match' if matched else ''}")
        return res

    say("TIER 0  no trustworthy ATS account matched")

    # ---- Tier 1: follow the company's own website ------------------------
    if not job.company_slug:
        res.error = "no linkedin company handle"
        say("TIER 1  no company handle on the posting -- skipping")
        return res
    try:
        website = _website_cached(job.company_slug)
    except li.RateLimited as e:
        res.error = "linkedin rate limited"
        say(f"TIER 1  {e}")
        return res

    if not website:
        res.error = "no company website listed"
        say("TIER 1  LinkedIn lists no website for this company")
        return res

    say(f"TIER 1  website {website}")
    refs, visited = site_mod.crawl(website)
    for u in visited:
        say(f"TIER 1    visited {u}")

    if not refs:
        say("TIER 1  no ATS link in the raw HTML -- JavaScript site")
        return _tier2(res, job, website, say, use_tier2, picker, browser)

    ref = refs[0]
    say(f"TIER 1  found {ref.provider} ref: {ref.slug or ref.url}")

    # Confirm the link we found is real by asking that ATS directly.
    if ref.slug and ref.provider in ats_mod.PROVIDERS_BY_ID:
        provider = ats_mod.PROVIDERS_BY_ID[ref.provider]
        confirm = ats_mod.probe_all_sync([ref.slug], providers=[provider])
        if confirm:
            hit = confirm[0]
            matched = title_match(job.job_title, hit.jobs)
            res.board_url = hit.board_url
            res.provider = hit.provider.id
            res.slug = hit.slug
            res.job_count = hit.count
            res.tier = 1
            res.confidence = score(True, matched, hit.count, from_company_site=True)
            res.evidence.append(Evidence("site_link", f"linked from {website}"))
            res.evidence.append(Evidence(
                "ats_api", f"{hit.provider.label} confirmed '{hit.slug}' "
                           f"with {hit.count} jobs"))
            if matched:
                res.evidence.append(Evidence("title_match", f"board lists {matched!r}"))
            say(f"TIER 1  CONFIRMED {hit.provider.label} ({hit.count} jobs)")
            return res

    # Workday and Personio have no public list API -- the URL we found on the
    # company's own site is the answer, we just can't verify the job count.
    res.board_url = ref.url
    res.provider = ref.provider
    res.slug = ref.slug
    res.tier = 1
    res.confidence = score(False, None, 0, from_company_site=True)
    res.evidence.append(Evidence(
        "site_link", f"found on {website}; no public API to verify against"))
    say(f"TIER 1  UNVERIFIED {ref.provider} {ref.url}")
    return res


def _tier2(res, job, website, say, enabled, picker, browser) -> Resolution:
    """Tier 2: render the site in a real browser and let a picker navigate."""
    if not enabled:
        res.error = "needs browser agent (tier 2)"
        return res

    if picker is None:
        picker, kind = agent_mod.default_picker()
    else:
        kind = "custom"
    say(f"TIER 2  browser agent ({kind} picker) from {website}")

    try:
        out = agent_mod.run(website, picker=picker, browser=browser)
    except Exception as e:                                  # noqa: BLE001
        res.error = f"tier 2 failed: {e}"
        say(f"TIER 2  error: {e}")
        return res

    for s_ in out.steps:
        say(f"TIER 2    {s_}")

    if not out.board_url:
        res.error = "tier 2 found no job listing"
        say("TIER 2  no listing found")
        return res

    res.board_url = out.board_url
    res.provider = out.ats_provider
    res.slug = out.ats_slug
    res.tier = 2

    # If the agent surfaced an ATS account, confirm it the same way Tier 0
    # does -- a rendered link is a claim; the API answering is proof.
    if out.ats_slug and out.ats_provider in ats_mod.PROVIDERS_BY_ID:
        provider = ats_mod.PROVIDERS_BY_ID[out.ats_provider]
        confirm = ats_mod.probe_all_sync([out.ats_slug], providers=[provider])
        if confirm:
            hit = confirm[0]
            matched = title_match(job.job_title, hit.jobs)
            res.board_url = hit.board_url
            res.job_count = hit.count
            res.confidence = score(True, matched, hit.count, from_company_site=True)
            res.evidence.append(Evidence(
                "browser_agent", f"navigated {len(out.hops)} pages from {website}"))
            res.evidence.append(Evidence(
                "ats_api", f"{hit.provider.label} confirmed '{hit.slug}' "
                           f"with {hit.count} jobs"))
            if matched:
                res.evidence.append(Evidence("title_match", f"board lists {matched!r}"))
            say(f"TIER 2  CONFIRMED {hit.provider.label} ({hit.count} jobs)")
            return res

    # A company hosting its own listing page is a legitimate answer -- there's
    # just no API to verify it against.
    res.confidence = score(False, None, 0, from_company_site=True)
    res.evidence.append(Evidence(
        "browser_agent", f"reached a page listing multiple jobs at {out.board_url}"))
    say(f"TIER 2  listing page (unverified) {out.board_url}")
    return res
