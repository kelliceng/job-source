"""Step 1 of the pipeline: LinkedIn job URL -> company name.

We use LinkedIn's *guest* endpoint, which serves a job posting to logged-out
visitors. It returns a small HTML fragment (~33KB) instead of the full 330KB
page, and crucially it contains only THIS job's company -- the full page also
lists "similar jobs" from other companies, which is an easy way to grab the
wrong name.
"""
import re
from typing import Optional

import httpx

from .models import LinkedInJob

class RateLimited(Exception):
    """LinkedIn returned 429. Distinct from 'this company has no website' --
    conflating the two makes the failure report meaningless."""


GUEST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def extract_job_id(url: str) -> Optional[str]:
    """Pull the numeric job id out of any of LinkedIn's job URL shapes."""
    patterns = [
        r"/jobs/view/(?:[^/]*-)?(\d{6,})",       # /jobs/view/4427787182 or /jobs/view/title-at-co-4427787182
        r"[?&]currentJobId=(\d{6,})",            # /jobs/search/?currentJobId=4427787182
        r"[?&]refId=.*?(\d{10,})",
        r"/(\d{10,})(?:[/?#]|$)",                # bare id at the end
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _unescape(s: str) -> str:
    import html
    return html.unescape(s).strip()


def fetch_job(url: str, client: Optional[httpx.Client] = None) -> LinkedInJob:
    """Read a LinkedIn job posting without logging in.

    Raises ValueError if the URL isn't a job URL or LinkedIn won't serve it.
    """
    job_id = extract_job_id(url)
    if not job_id:
        raise ValueError(f"Could not find a LinkedIn job id in: {url}")

    owns_client = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True,
                                    headers={"User-Agent": UA})
    try:
        r = client.get(GUEST_ENDPOINT.format(job_id=job_id))
        if r.status_code == 429:
            raise RateLimited(f"LinkedIn rate-limited job {job_id} (HTTP 429)")
        if r.status_code != 200:
            raise ValueError(f"LinkedIn returned HTTP {r.status_code} for job {job_id}")
        html_text = r.text
    finally:
        if owns_client:
            client.close()

    # Company name: the top-card org link is the most reliable spot.
    company = None
    for pat in (r'topcard__org-name-link[^>]*>\s*([^<]+)',
                r'"companyName"\s*:\s*"([^"]+)"',
                r'topcard__flavor[^>]*>\s*([^<]+)'):
        m = re.search(pat, html_text, re.I)
        if m:
            company = _unescape(m.group(1))
            break

    # Company slug from the /company/<slug> link -- useful as a fallback
    # spelling when the display name doesn't slugify cleanly.
    slug_m = re.search(r'linkedin\.com/company/([a-z0-9\-\.]+)', html_text, re.I)
    company_slug = slug_m.group(1).lower() if slug_m else None

    title_m = re.search(r'topcard__title[^>]*>\s*([^<]+)', html_text, re.I)
    loc_m = re.search(r'topcard__flavor--bullet[^>]*>\s*([^<]+)', html_text, re.I)

    # "Apply on company website" sometimes links straight to the ATS.
    ext = None
    ext_m = re.search(r'href="(https?://(?!www\.linkedin\.com)[^"]+)"[^>]*'
                      r'(?:apply|externalApply)', html_text, re.I)
    if ext_m:
        ext = _unescape(ext_m.group(1))

    if not company:
        raise ValueError(f"Could not read a company name from LinkedIn job {job_id}")

    return LinkedInJob(
        job_id=job_id,
        company_name=company,
        company_slug=company_slug,
        job_title=_unescape(title_m.group(1)) if title_m else None,
        location=_unescape(loc_m.group(1)) if loc_m else None,
        external_apply_url=ext,
    )


COMPANY_PAGE = "https://www.linkedin.com/company/{slug}"


def fetch_company_website(company_slug: str,
                          client: Optional[httpx.Client] = None) -> Optional[str]:
    """Get a company's own website from its public LinkedIn page.

    LinkedIn doesn't put the URL in plain sight -- it wraps it in a redirect
    link tagged `trk=about_website`, with the destination percent-encoded in
    the `url=` parameter (dots encoded as %2E). We pull it back out.
    """
    from urllib.parse import unquote, parse_qs, urlparse

    owns_client = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True,
                                    headers={"User-Agent": UA})
    try:
        r = client.get(COMPANY_PAGE.format(slug=company_slug))
        if r.status_code == 429:
            raise RateLimited(f"LinkedIn rate-limited /company/{company_slug} (HTTP 429)")
        if r.status_code != 200:
            return None
        text = r.text
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            client.close()

    m = re.search(r'href="([^"]*/redir/redirect\?[^"]*trk=about_website[^"]*)"', text)
    if m:
        href = _unescape(m.group(1))
        qs = parse_qs(urlparse(href).query)
        if qs.get("url"):
            return unquote(qs["url"][0])

    # Fallback: the only non-LinkedIn outbound link on the page is usually it.
    for u in re.findall(r'href="(https?://[^"]+)"', text):
        u = _unescape(u)
        host = urlparse(u).netloc.lower()
        if not re.search(r"(linkedin\.com|licdn\.com|lnkd\.in|bing\.com|google\.)", host):
            return u
    return None
