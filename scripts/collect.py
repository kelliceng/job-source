#!/usr/bin/env python3
"""Collect random LinkedIn job URLs for the test set, via LinkedIn's public
guest search endpoint (the same one the logged-out 'see more jobs' button hits).

We deliberately spread across industries, seniorities and countries -- a test
set of 20 US startups would flatter the approach and prove nothing.
"""
import json
import random
import re
import sys
import time

import httpx

SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
          "?keywords={kw}&location={loc}&start={start}")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

QUERIES = [
    ("software engineer", "United States"), ("data analyst", "United Kingdom"),
    ("product manager", "Germany"),         ("nurse", "United States"),
    ("accountant", "Canada"),               ("marketing manager", "France"),
    ("mechanical engineer", "United States"), ("teacher", "Australia"),
    ("sales representative", "Netherlands"), ("logistics coordinator", "Singapore"),
    ("civil engineer", "India"),            ("graphic designer", "Spain"),
]


def collect(target=40):
    out = {}
    with httpx.Client(timeout=20.0, headers={"User-Agent": UA},
                      follow_redirects=True) as c:
        for kw, loc in QUERIES:
            if len(out) >= target:
                break
            url = SEARCH.format(kw=kw.replace(" ", "%20"),
                                loc=loc.replace(" ", "%20"),
                                start=random.choice([0, 25, 50]))
            try:
                r = c.get(url)
            except httpx.HTTPError as e:
                print(f"  ! {kw}/{loc}: {e}", file=sys.stderr); continue
            if r.status_code != 200:
                print(f"  ! {kw}/{loc}: HTTP {r.status_code}", file=sys.stderr); continue

            ids = re.findall(r'data-entity-urn="urn:li:jobPosting:(\d+)"', r.text)
            names = re.findall(r'hidden-nested-link[^>]*>\s*([^<]+)', r.text)
            for i, jid in enumerate(ids):
                if jid in out:
                    continue
                out[jid] = {
                    "job_id": jid,
                    "url": f"https://www.linkedin.com/jobs/view/{jid}/",
                    "seen_company": names[i].strip() if i < len(names) else None,
                    "query": f"{kw} / {loc}",
                }
            print(f"  {kw:22s} {loc:16s} -> {len(ids):3d} jobs "
                  f"(total {len(out)})", file=sys.stderr)
            time.sleep(1.2)   # be polite; this endpoint rate-limits
    return list(out.values())


if __name__ == "__main__":
    jobs = collect()
    random.seed(20260827)
    random.shuffle(jobs)
    with open("data/testset.json", "w") as f:
        json.dump(jobs, f, indent=2)
    print(f"\nwrote {len(jobs)} jobs to data/testset.json", file=sys.stderr)
