"""Deciding how much to trust an answer.

Returning a URL is easy. Returning a URL with evidence attached is the part
that matters -- and it's what lets the system say "I don't know" instead of
guessing confidently.
"""
import re
from typing import List, Optional, Tuple

_STOP = {"the", "a", "an", "of", "and", "for", "to", "in", "at", "senior", "sr",
         "junior", "jr", "staff", "lead", "principal", "i", "ii", "iii"}


def _tokens(s: str) -> set:
    s = re.sub(r"[^a-z0-9\s]", " ", (s or "").lower())
    return {w for w in s.split() if w and w not in _STOP}


def title_match(linkedin_title: Optional[str],
                board_jobs: List[Tuple[str, str]]) -> Optional[str]:
    """Is the job we started from actually on this board?

    This is the strongest signal available. It proves we found the *right*
    company, not merely a real account that happens to share a name.
    """
    if not linkedin_title or not board_jobs:
        return None
    want = _tokens(linkedin_title)
    if not want:
        return None

    best, best_score = None, 0.0
    for title, _url in board_jobs:
        have = _tokens(title)
        if not have:
            continue
        overlap = len(want & have) / len(want)
        if overlap > best_score:
            best, best_score = title, overlap
    # Two-thirds of the meaningful words lining up is a match; job titles get
    # reworded slightly between LinkedIn and the company's own board.
    return best if best_score >= 0.67 else None


def score(ats_confirmed: bool, matched_title: Optional[str],
          job_count: int = 0, from_company_site: bool = False) -> float:
    """Confidence, 0-1. Deliberately conservative."""
    if ats_confirmed and matched_title:
        return 0.98      # the API answered AND our exact job is on the board
    if ats_confirmed and from_company_site:
        return 0.92      # we followed a link from their own site, API confirmed
    if ats_confirmed:
        return 0.80      # real account, right name -- job may have just closed
    if from_company_site and job_count == 0:
        return 0.55      # found via their site but nothing verified it
    return 0.35


# --- Does this page actually LIST jobs? --------------------------------------

_JOB_PATH = re.compile(r"/(jobs?|careers?|opening|position|vacanc|role)s?/"
                       r"[^/]{4,}$|/[0-9a-f]{8}-[0-9a-f]{4}-", re.I)


def listing_signals(links, page_text: str = "", page_url: str = "",
                    page_title: str = "") -> dict:
    """Score how much a page looks like a list of open roles.

    A careers *landing* page says "we're hiring, come join us". A job *listing*
    page has many links that share a URL shape and differ only in the job. The
    brief asks for the listing, so we measure the difference.
    """
    from collections import Counter
    from urllib.parse import urlparse

    shapes = Counter()
    for l in links:
        p = urlparse(l.href)
        segs = [s for s in p.path.split("/") if s]
        if len(segs) >= 2:
            # Group by the path prefix, ignoring the final identifying segment.
            shapes["/".join(segs[:-1])] += 1

    best_shape, repeat = ("", 0)
    if shapes:
        best_shape, repeat = shapes.most_common(1)[0]

    detail_like = sum(1 for l in links if _JOB_PATH.search(urlparse(l.href).path))
    distinct_titles = len({(l.text or "").strip().lower()
                           for l in links if len((l.text or "").strip()) > 8})

    # A repeated URL shape alone is not enough -- every homepage has one
    # (/en-US/platform/..., /blog/..., /docs/...). The shape has to actually
    # be about jobs, or the links have to look like job postings.
    shape_is_jobs = bool(re.search(r"(job|career|opening|position|vacanc|role)",
                                   best_shape, re.I))

    # Not every job board encodes "job" in its URLs. If the page itself is
    # clearly a jobs page (by URL or title) and it carries a pile of distinct
    # multi-word links, that's a listing too.
    context = f"{page_url} {page_title}"
    page_is_jobs = bool(re.search(
        r"(jobs?|careers?|open[-\s]?positions?|openings?|vacanc|stellen|"
        r"emplois?|recrut)", context, re.I))
    wordy = sum(1 for l in links
                if len((l.text or "").strip().split()) >= 2
                and len((l.text or "").strip()) > 10)

    return {
        "page_is_jobs": page_is_jobs,
        "wordy_links": wordy,
        "repeated_shape": best_shape,
        "repeat_count": repeat,
        "detail_like": detail_like,
        "distinct_titles": distinct_titles,
        "shape_is_jobs": shape_is_jobs,
        "is_listing": (shape_is_jobs and repeat >= 5 and distinct_titles >= 5)
                      or detail_like >= 5
                      or (page_is_jobs and wordy >= 8 and distinct_titles >= 8),
    }
