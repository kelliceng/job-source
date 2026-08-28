"""Tier 2, part 2: decide where to click.

Two interchangeable backends make the decision:

  * `heuristic` -- hand-written scoring. Free, instant, no API key. Good on
    conventionally built sites.
  * `llm`       -- Claude reads the compact link inventory and picks. Handles
    the cases rules can't: "Join the crew", "Nous recrutons", links buried in
    a mega-menu, non-English sites.

Both return the same Decision, so the ladder doesn't care which ran. That also
means the whole browser pipeline is testable without an API key, and the model
is a swap-in rather than a dependency.
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .browser import Browser, Link, Page
from .site import find_ats, normalize_board_url
from .verify import listing_signals

MODEL = "claude-opus-5"

GOAL = ("Find the page on this company's website that LISTS their current open "
        "job openings -- not a culture or 'life at' page, and not a single job "
        "posting.")


@dataclass
class Decision:
    action: str                 # "click" | "done" | "give_up"
    href: Optional[str] = None
    reason: str = ""


@dataclass
class AgentResult:
    board_url: Optional[str] = None
    ats_provider: Optional[str] = None
    ats_slug: Optional[str] = None
    is_listing: bool = False
    hops: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    llm_calls: int = 0


# --------------------------------------------------------------------------
# Backend A: rules
# --------------------------------------------------------------------------

_GOOD = re.compile(r"\b(careers?|jobs?|open\s+(roles|positions)|join\s+\w+|"
                   r"we[''`]?re\s+hiring|opportunities|vacancies|openings|"
                   r"work\s+(with|at|for)\s+us)\b", re.I)
_BAD = re.compile(r"\b(blog|news|press|privacy|terms|cookie|contact|login|"
                  r"sign\s?in|investor|newsletter)\b", re.I)


def choose_heuristic(page: Page, visited: set) -> Decision:
    best, best_score = None, 0.0
    for l in page.links:
        if l.href.split("#")[0].rstrip("/") in visited:
            continue
        blob = f"{l.text} {l.label} {l.href}"
        if _BAD.search(blob):
            continue
        score = 0.0
        if _GOOD.search(l.text or l.label):
            score += 3
        if _GOOD.search(l.href):
            score += 2
        if find_ats(l.href):
            score += 8          # a direct ATS link is the jackpot
        if l.zone == "footer":
            score += 1.5        # careers almost always lives in the footer
        elif l.zone == "nav":
            score += 1.0
        if score > best_score:
            best, best_score = l, score
    if best and best_score >= 3:
        return Decision("click", best.href, f"rule score {best_score:.1f}")
    return Decision("give_up", reason="no link scored high enough")


# --------------------------------------------------------------------------
# Backend B: Claude
# --------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["click", "done", "give_up"]},
        "href": {"type": "string",
                 "description": "The exact destination to click, copied from "
                                "the list. Empty for done/give_up."},
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["action", "href", "reason"],
    "additionalProperties": False,
}


# Anything that might plausibly be the answer. Deliberately loose -- this
# decides what the model is allowed to SEE, not what it should pick, so a
# false positive costs one line and a false negative costs the whole task.
_RELEVANT = re.compile(
    r"(career|job|join|hiring|vacanc|opening|position|recruit|opportunit|"
    r"work[-\s]?(with|at|for)|team|people|about|company|emplo|stellen|"
    r"karriere|carri[eè]re|empleo|lavor)", re.I)


def _inventory(page: Page, limit: int = 90) -> str:
    """The compact page description we send instead of raw HTML.

    Relevance first, then page structure. An earlier version just walked
    footer -> nav -> body and truncated at the limit; on a 125-link page that
    silently dropped the "Careers" link before the model ever saw it, and the
    model then correctly reported there was nothing to click. What you feed
    the model matters more than which model it is.
    """
    lines, seen = [], set()

    def add(l) -> bool:
        line = l.describe(page.url)
        if line in seen or len(line) > 160:
            return False
        seen.add(line)
        lines.append(line)
        return len(lines) >= limit

    # 1. Every plausibly relevant link, wherever it sits on the page.
    for l in page.links:
        if _RELEVANT.search(f"{l.text} {l.label} {l.href}") and add(l):
            return "\n".join(lines)
    # 2. Fill the remaining budget with structural context.
    for zone in ("footer", "nav", "body"):
        for l in page.links:
            if l.zone == zone and add(l):
                return "\n".join(lines)
    return "\n".join(lines)


def build_prompt(page: Page, visited: set) -> str:
    """The one prompt every backend sees. Keeping it shared means switching
    providers compares the models, not two different prompts."""
    return (
        f"{GOAL}\n\n"
        f"Current page: {page.url}\n"
        f"Title: {page.title}\n\n"
        f"Page text (first 400 chars):\n{page.text_sample[:400]}\n\n"
        f"Links on this page ([zone] text -> destination):\n"
        f"{_inventory(page)}\n\n"
        f"Already visited: {sorted(visited) if visited else 'nothing yet'}\n\n"
        "If this page already lists multiple open jobs, answer 'done'. "
        "Otherwise pick the single link most likely to lead there and "
        "answer 'click' with its exact destination copied from above. "
        "If nothing here can lead to job listings, answer 'give_up'. "
        "Prefer a link to an external job board (Greenhouse, Lever, Ashby, "
        "Workday, SmartRecruiters) over a page on the company's own site."
    )


def _to_decision(raw: str, page: Page) -> Decision:
    d = json.loads(raw)
    href = (d.get("href") or "").strip()
    if href and not href.startswith("http"):
        from urllib.parse import urljoin
        href = urljoin(page.url, href)
    return Decision(d.get("action", "give_up"), href or None, d.get("reason", ""))


def make_anthropic_picker(client=None, model: str = MODEL):
    """Claude-backed picker. Paid API; the most capable option."""
    import anthropic
    client = client or anthropic.Anthropic()

    def choose(page: Page, visited: set) -> Decision:
        prompt = build_prompt(page, visited)
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            # Picking a link from a list is a simple task -- low effort keeps
            # this fast and cheap without hurting the decision.
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        return _to_decision(
            next(b.text for b in resp.content if b.type == "text"), page)

    return choose


# --------------------------------------------------------------------------
# Backend C: Gemini (has a free tier -- no card required)
# --------------------------------------------------------------------------

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


def gemini_client(api_key: Optional[str] = None):
    from google import genai
    key = (api_key or os.environ.get("GEMINI_API_KEY")
           or os.environ.get("GOOGLE_API_KEY"))
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY (free from aistudio.google.com)")
    return genai.Client(api_key=key)


def available_models(client=None) -> List[str]:
    """What this key can actually call. Free-tier access varies by account,
    so we ask rather than assume a model is available."""
    client = client or gemini_client()
    out = []
    for m in client.models.list():
        name = (m.name or "").replace("models/", "")
        actions = getattr(m, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            out.append(name)
    return out


def pick_model(client=None, preferred: str = GEMINI_MODEL) -> str:
    """Use the preferred model if the key has it; otherwise the newest Flash
    it does have. Avoids hard-coding a model the account can't reach."""
    try:
        names = available_models(client)
    except Exception:
        return preferred
    if preferred in names:
        return preferred
    flash = sorted((n for n in names if "flash" in n and "live" not in n),
                   reverse=True)
    return flash[0] if flash else (names[0] if names else preferred)


# Free-tier capacity is shared and spiky -- 503 UNAVAILABLE and 429 are normal,
# not bugs. Retry with backoff, then try a different model before giving up.
_TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
              "500", "INTERNAL", "overloaded", "high demand")


def _is_transient(e: Exception) -> bool:
    msg = str(e)
    return any(t in msg for t in _TRANSIENT)


def model_chain(client=None, preferred: Optional[str] = None) -> List[str]:
    """Ordered models to try. Lite variants sit behind the main ones: they're
    less capable but far less likely to be saturated."""
    preferred = preferred or GEMINI_MODEL
    try:
        names = available_models(client)
    except Exception:
        return [preferred]
    usable = [n for n in names
              if "flash" in n and not any(x in n for x in
                                          ("image", "tts", "live", "embedding",
                                           "omni", "transcribe", "thinking"))]
    chain = []
    for n in [preferred] + sorted((u for u in usable if "lite" not in u),
                                  reverse=True) + \
             sorted((u for u in usable if "lite" in u), reverse=True):
        if n in names and n not in chain:
            chain.append(n)
    return chain or [preferred]


def make_gemini_picker(client=None, model: Optional[str] = None,
                       attempts: int = 3):
    """Gemini-backed picker. Free tier covers this workload comfortably.

    Retries transient capacity errors and falls through a chain of models
    rather than failing the whole navigation on one busy endpoint.
    """
    import time

    from google.genai import types
    client = client or gemini_client()
    chain = model_chain(client, model)
    model = chain[0]
    # Gemini accepts a narrower JSON Schema subset than Anthropic -- notably
    # it rejects additionalProperties. Strip what it doesn't take.
    schema = {k: v for k, v in _SCHEMA.items() if k != "additionalProperties"}

    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=schema,
        temperature=0,
    )

    def choose(page: Page, visited: set) -> Decision:
        prompt = build_prompt(page, visited)
        last = None
        for m in chain:
            for attempt in range(attempts):
                try:
                    resp = client.models.generate_content(
                        model=m, contents=prompt, config=cfg)
                    choose.model_name = m
                    return _to_decision(resp.text, page)
                except Exception as e:                      # noqa: BLE001
                    last = e
                    if not _is_transient(e):
                        raise
                    if attempt < attempts - 1:
                        time.sleep(1.5 * (2 ** attempt))    # 1.5s, 3s
            # this model stayed busy -- move to the next one in the chain
        raise RuntimeError(f"all Gemini models busy; last error: {last}")

    choose.model_name = model
    choose.chain = chain
    return choose


def make_cascade_picker(llm=None):
    """Rules first; the model only when the rules give up.

    Measured on a 14-URL held-out set, the heuristic and Gemini each scored
    86% -- but on *different* URLs. The heuristic uniquely got Northrop
    Grumman; Gemini uniquely got Allianz Direct, a German site whose careers
    link reads "karriere". They're complementary, not ranked.

    So run the free, instant one first and pay for the model only on the pages
    where rules genuinely run out. Most pages never reach the model.
    """
    llm = llm or make_gemini_picker()

    def choose(page: Page, visited: set) -> Decision:
        d = choose_heuristic(page, visited)
        if d.action == "click" and d.href:
            return d
        try:
            out = llm(page, visited)
            out.reason = f"(rules gave up) {out.reason}"
            return out
        except Exception as e:                              # noqa: BLE001
            return Decision("give_up", reason=f"rules gave up; llm failed: {e}")

    choose.llm = llm
    return choose


def default_picker():
    """Pick the best backend the environment can actually reach.

    Gemini first because it has a free tier -- this take-home shouldn't
    require a paid account to run.
    """
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        try:
            p = make_cascade_picker()
            model = getattr(p.llm, "model_name", GEMINI_MODEL)
            return p, f"cascade:{model}"
        except Exception as e:                              # noqa: BLE001
            print(f"  (gemini unavailable: {e})")
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            return make_anthropic_picker(), f"anthropic:{MODEL}"
        except Exception:
            pass
    return choose_heuristic, "heuristic"


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def run(start_url: str, picker=None, max_hops: int = 3,
        browser: Optional[Browser] = None) -> AgentResult:
    picker = picker or choose_heuristic
    res = AgentResult()
    owns = browser is None
    b = browser or Browser()
    if owns:
        b.__enter__()
    try:
        url, visited = start_url, set()
        for hop in range(max_hops + 1):
            page = b.visit(url)
            if page is None:
                res.steps.append(f"hop {hop}: could not load {url}")
                break
            visited.add(page.url.split("#")[0].rstrip("/"))
            res.hops.append(page.url)

            # Did we land on -- or can we see -- a real ATS board?
            refs = find_ats(page.url + " " +
                            " ".join(str(l.href or "") for l in page.links))
            if refs:
                ref = refs[0]
                res.board_url = normalize_board_url(ref.url)
                res.ats_provider, res.ats_slug = ref.provider, ref.slug
                res.is_listing = True
                res.steps.append(f"hop {hop}: found {ref.provider} board")
                return res

            sig = listing_signals(page.links, page.text_sample,
                                  page.url, page.title)
            if sig["is_listing"]:
                # A company hosting its own listing is a valid answer, but the
                # ATS board behind it is the better one. One extra hop into a
                # job detail page usually reveals the "Apply" link's real host.
                res.board_url = page.url
                res.is_listing = True
                res.steps.append(
                    f"hop {hop}: page lists jobs "
                    f"({sig['repeat_count']} similar links, "
                    f"{sig['distinct_titles']} distinct titles)")
                deep = _peek_for_ats(b, page)
                if deep:
                    res.ats_provider, res.ats_slug = deep.provider, deep.slug
                    res.board_url = normalize_board_url(deep.url)
                    res.steps.append(f"hop {hop}: job page revealed "
                                     f"{deep.provider} board")
                return res

            if hop == max_hops:
                res.steps.append(f"hop {hop}: out of hops")
                break

            d = picker(page, visited)
            if isinstance(d, tuple):
                d = d[0]
            res.steps.append(f"hop {hop}: {d.action} {d.href or ''} ({d.reason})")
            if d.action != "click" or not d.href:
                break
            nxt = d.href.split("#")[0].rstrip("/")
            if nxt in visited:
                res.steps.append("stopped: model chose a page we already saw")
                break
            url = d.href
        return res
    finally:
        if owns:
            b.__exit__(None, None, None)


def _peek_for_ats(b: Browser, page: Page):
    """Open one job posting and look for the ATS behind the Apply button."""
    sig_links = [l for l in page.links
                 if re.search(r"/(careers?|jobs?|positions?)/[^/]{6,}$",
                              l.href) and len((l.text or "").strip()) > 8]
    if not sig_links:
        return None
    detail = b.visit(sig_links[0].href)
    if detail is None:
        return None
    refs = find_ats(detail.url + " " +
                    " ".join(str(l.href or "") for l in detail.links))
    return refs[0] if refs else None
