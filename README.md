# LinkedIn Job Source Agent

Takes a LinkedIn job posting URL and returns the company's own job listing page.

```
https://www.linkedin.com/jobs/view/4427787182/
  -> https://jobs.ashbyhq.com/harvey        (0.9s, confidence 0.98)
```

## The idea

Almost no company builds its own jobs page — they rent one from an ATS
(Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Recruitee,
Breezy). So this isn't a browsing problem, it's a lookup problem: *which* ATS,
and *which account* on it.

Every one of those platforms publishes its customers' open jobs at a
predictable address, readable with no login and no API key. That gives us both
a fast path and — more importantly — a way to **prove** an answer is right
instead of asserting it.

## The ladder

Each tier is slower and costlier than the one above it, so most lookups never
get past the first two.

| Tier | Method | Speed | Cost |
|------|--------|-------|------|
| 0 | Guess the ATS account name from the company name, then ask each ATS API whether it exists | ~0.9s | free |
| 1 | Follow the company's website to their careers page, look for an ATS link or embedded widget | ~3-6s | free |
| 2 | Real browser (Playwright) + a picker chooses where to click — for JavaScript-rendered sites | ~8-20s | ~1¢ |
| 3 | *(not built yet)* Web search — the only route to unguessable Workday tenants | ~2s | metered |

## Tier 2: the browser agent

Tier 1 downloads HTML as text, so it fails on sites that assemble themselves
with JavaScript. Harvey's careers page is 333KB in which the string `ashby`
appears **zero times** — the link only exists after the page runs.

Tier 2 opens a real browser, then loops: render the page → extract a compact
**link inventory** → decide where to click → repeat, up to 3 hops.

**We send the model a link inventory, not the page.** A rendered page is
several hundred KB, nearly all styling and scripts — expensive, slow, and
harder to reason about. The inventory is ~40 lines of
`[zone] link text -> destination`, where zone is `nav` / `footer` / `body`.
Footer and nav links are listed first because careers links live there.

Three interchangeable pickers make the decision:

| Picker | Needs | Notes |
|--------|-------|-------|
| `heuristic` | nothing | Hand-written scoring. Free, instant, no key. |
| `gemini` | `GEMINI_API_KEY` | **Free tier, no card.** Key from [aistudio.google.com](https://aistudio.google.com/apikey). |
| `anthropic` | `ANTHROPIC_API_KEY` | Paid; most capable. |

All three return the same `Decision`, so the ladder doesn't care which ran, and
the whole browser pipeline is testable without any key. They also share one
`build_prompt()` — so switching providers compares the *models*, not two
accidentally different prompts.

`default_picker()` returns the **cascade** (rules first, model on give-up) when
a Gemini key is present, then Anthropic, then bare rules.
Model choice isn't hard-coded: `pick_model()` asks the key which models it can
actually call and picks the newest Flash available, since free-tier access
varies by account.

```bash
export GEMINI_API_KEY=...          # free from aistudio.google.com
python scripts/check_llm.py --live # confirm it works before a full run
```

### Where the rules break (and the model doesn't)

- **Ekimetrics** — the careers link reads "Join Ekimetrics" / "Life at Eki".
  No amount of keyword tuning generalises to every phrasing on earth.
- **DocuWare** — the rules reached `careers.docuware.com/jobs`, then clicked
  *away* to "partnership opportunities", because they can't tell they'd
  arrived. A model answers `done`.

### One extra hop worth knowing about

Harvey's careers page never links to Ashby — it's a self-hosted listing, and
the Ashby link is behind the "Apply" button on each individual posting. So when
the agent lands on a listing page, it opens one posting and looks for the ATS
there. That's what turns `harvey.ai/en-US/careers` into
`jobs.ashbyhq.com/harvey`, which is what the brief asks for.

### Does the LLM actually help? I measured it.

The honest answer is *not on its own*. Two test sets, 7 ATS providers, same
ladder, only the Tier 2 picker swapped:

| Set | Heuristic | Gemini | Cascade |
|-----|-----------|--------|---------|
| Tuned set (20 URLs) | 75% | 75% | — |
| **Held-out (14 URLs)** | **86%** | **86%** | **93%** |

The first row is not a fair comparison — I tuned the heuristic's keywords
*on those 20 URLs* after watching it fail, which is textbook overfitting. The
held-out set is the honest one.

On held-out, the heuristic and Gemini both scored 86% — but **they failed on
different URLs**:

- Gemini uniquely got **Allianz Direct** — a German site whose careers link
  reads *"karriere"*. No English keyword list reaches that.
- The heuristic uniquely got **Northrop Grumman**.
- Both missed GM Financial.

So the model isn't better than the rules here; it's *differently* good. That
makes the right design a **cascade**: run the free, instant heuristic first and
call the model only on pages where the rules give up. That scored 93% — better
than either alone — while most pages never reach the model at all.

Caveat worth stating plainly: n=14 is small, and a one-URL difference is within
noise. The cascade result is the robust one because it doesn't depend on which
picker is "better".

## Verification

Returning a URL is easy. Every answer carries evidence and a confidence score:

- **`ats_api`** — the platform confirmed the account and returned N open jobs
- **`title_match`** — the exact job we started from appears on that board.
  This is the strongest signal: it proves we found the *right* company, not
  just a real account with a similar name.
- **`site_link`** — we followed a link from the company's own website

Answers below the confidence threshold are reported as failures rather than
returned. A resolver that knows when it failed is more useful than one that
always answers.

### Why that matters — a real example

"Universal Music Group UK" generates the candidate slug `universal`, which
*does* exist on Greenhouse — with two jobs, "Freelance Designer" and "Open
Applicatons" [sic]. It's an unrelated company that grabbed a common word.

Slugs derived from truncating a company name are marked **weak**, and a weak
match is only accepted if the original job title also appears on that board.
Without that rule this returns a confidently wrong answer.

## Usage

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

./.venv/bin/python scripts/resolve.py "https://www.linkedin.com/jobs/view/4427787182/"
./.venv/bin/python scripts/resolve.py --json "<url>"

./.venv/bin/python scripts/collect.py       # build a random test set
./.venv/bin/python scripts/evaluate.py 20   # run it, print success rate by tier
```

## Layout

```
jobsource/
  linkedin.py   read a job posting + company website, logged out
  slugs.py      company name -> candidate ATS account names (with strength)
  ats.py        the 7 ATS APIs; parallel probing in waves
  site.py       tier 1 crawl; ATS detection; board URL normalisation
  verify.py     title matching and confidence scoring
  resolver.py   the ladder
  cache.py      on-disk cache (LinkedIn rate-limits hard)
```

## Notes

- LinkedIn returns **HTTP 429** aggressively. Responses are cached for a week
  and rate limiting is reported as its own failure mode, not silently folded
  into "no website found".
- Found URLs are normalised back to the *listing* page: a Workday link found in
  the wild is usually a single posting, and
  `…/UMGAPAC/job/Singapore/Creative-Intern_UMG-17166` becomes `…/UMGAPAC`.
