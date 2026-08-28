"""Shared data shapes used across every tier of the resolver."""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class LinkedInJob:
    """What we can read off a LinkedIn job posting without logging in."""
    job_id: str
    company_name: str
    company_slug: Optional[str] = None   # e.g. "harvey-ai" from /company/harvey-ai
    job_title: Optional[str] = None
    location: Optional[str] = None
    external_apply_url: Optional[str] = None  # sometimes points straight at the ATS


@dataclass
class Evidence:
    """One reason we believe an answer is correct. Collected, not summarised away."""
    kind: str      # "ats_api" | "title_match" | "listing_shape" | "embed_token" | ...
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


@dataclass
class Resolution:
    """The final answer, plus how we got there and how sure we are."""
    linkedin_url: str
    company_name: Optional[str] = None
    board_url: Optional[str] = None
    provider: Optional[str] = None       # "ashby", "lever", ...
    slug: Optional[str] = None
    job_count: Optional[int] = None
    confidence: float = 0.0              # 0.0 - 1.0
    tier: Optional[int] = None           # which tier produced the answer
    evidence: list = field(default_factory=list)
    trace: list = field(default_factory=list)   # every step we tried, in order
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.board_url is not None

    def log(self, msg: str) -> None:
        self.trace.append(msg)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = [str(e) for e in self.evidence]
        d["ok"] = self.ok
        return d
