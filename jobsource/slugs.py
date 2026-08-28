"""Turn a company's display name into the account names it might use on an ATS.

"Harvey"            -> harvey, harveyai, harvey-ai
"Perkins Eastman"   -> perkinseastman, perkins-eastman, perkins
"Ekimetrics"        -> ekimetrics

We generate a handful of plausible spellings and let the ATS APIs tell us which
one is real. Guessing is cheap; the check is authoritative.
"""
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class SlugCandidate:
    """A guessed account name, plus how much we'd trust a bare match on it.

    `strong` slugs are derived from the company's full name ("perkinseastman")
    or LinkedIn's own handle. `weak` slugs are truncations ("universal" from
    "Universal Music Group UK") -- these collide with unrelated companies that
    happened to grab a common word, so a weak match needs corroborating
    evidence before we believe it.
    """
    slug: str
    strong: bool = True

    def __str__(self) -> str:
        return self.slug

# Words companies put in their legal name that never appear in an ATS slug.
_NOISE = {
    "inc", "inc.", "llc", "l.l.c", "ltd", "ltd.", "limited", "corp", "corp.",
    "corporation", "co", "co.", "company", "gmbh", "ag", "sa", "s.a", "sas",
    "bv", "b.v", "nv", "ab", "as", "oy", "plc", "pte", "pty", "srl", "spa",
    "group", "holdings", "holding", "the",
}


def _clean(name: str) -> str:
    # Drop anything in brackets: "Harvey (formerly Counsel AI)" -> "Harvey"
    name = re.sub(r"[\(\[].*?[\)\]]", " ", name)
    # Split on separators companies use for regional suffixes
    name = re.split(r"\s+[\|–—\-]\s+", name)[0]
    name = name.lower()
    name = name.replace("&", " and ")
    name = re.sub(r"[^a-z0-9\s\-]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _words(name: str) -> List[str]:
    return [w for w in _clean(name).replace("-", " ").split() if w and w not in _NOISE]


def candidates(company_name: str, linkedin_slug: Optional[str] = None,
               limit: int = 8) -> List[SlugCandidate]:
    """Ordered best-guess slugs. Most likely first, so we can stop early."""
    out: List[SlugCandidate] = []
    seen = set()

    def add(s: Optional[str], strong: bool = True) -> None:
        if not s:
            return
        s = s.strip("-").lower()
        # ATS slugs are short and simple; anything else is a bad guess.
        if 2 <= len(s) <= 40 and re.fullmatch(r"[a-z0-9][a-z0-9\-]*", s) and s not in seen:
            seen.add(s)
            out.append(SlugCandidate(s, strong))

    words = _words(company_name)
    joined = "".join(words)
    hyphen = "-".join(words)

    add(joined)                     # perkinseastman
    add(hyphen)                     # perkins-eastman

    if linkedin_slug:
        add(linkedin_slug)                          # harvey-ai
        add(linkedin_slug.replace("-", ""))         # harveyai
        # LinkedIn often appends -ai / -inc / -hq to disambiguate; strip it.
        add(re.sub(r"-(ai|inc|io|hq|app|labs?|tech|global|group)$", "", linkedin_slug))

    if len(words) > 1:
        add(words[0], strong=False)  # perkins -- a truncation, so: weak
        # Drop a trailing descriptor: "acme technologies" -> "acme"
        if words[-1] in {"technologies", "technology", "labs", "lab", "software",
                         "systems", "solutions", "digital", "ai", "health"}:
            add("".join(words[:-1]), strong=False)
            add("-".join(words[:-1]), strong=False)

    return out[:limit]
