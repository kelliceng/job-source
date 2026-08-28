#!/usr/bin/env python3
"""Run the resolver over the test set and report the success rate by tier.

Usage: python scripts/evaluate.py [n] [--delay 1.5]
"""
import json
import sys
import time
from collections import Counter

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from jobsource.agent import default_picker
from jobsource.browser import Browser
from jobsource.resolver import resolve

RESET, BOLD, GRN, RED, YEL, DIM = "\033[0m", "\033[1m", "\033[32m", "\033[31m", "\033[33m", "\033[2m"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 20
    delay = 1.5
    if "--delay" in sys.argv:
        delay = float(sys.argv[sys.argv.index("--delay") + 1])

    off = 0
    if "--offset" in sys.argv:
        off = int(sys.argv[sys.argv.index("--offset") + 1])
    jobs = json.load(open("data/testset.json"))[off:off + n]
    rows = []
    if "--picker" in sys.argv:
        want = sys.argv[sys.argv.index("--picker") + 1]
        if want == "heuristic":
            from jobsource.agent import choose_heuristic
            picker, kind = choose_heuristic, "heuristic"
        elif want == "cascade":
            from jobsource.agent import make_cascade_picker
            picker = make_cascade_picker()
            kind = "cascade"
        elif want == "gemini":
            from jobsource.agent import make_gemini_picker
            picker = make_gemini_picker()
            kind = f"gemini:{getattr(picker, 'model_name', '?')}"
        else:
            picker, kind = default_picker()
    else:
        picker, kind = default_picker()

    print(f"{BOLD}Running {len(jobs)} LinkedIn URLs{RESET}  "
          f"(tier 2 picker: {kind})\n")
    # One browser for the whole run -- launching Chromium per URL dominates
    # the wall clock otherwise.
    browser = Browser()
    browser.__enter__()
    for i, j in enumerate(jobs, 1):
        t0 = time.time()
        try:
            r = resolve(j["url"], picker=picker, browser=browser)
        except Exception as e:                     # noqa: BLE001
            print(f"{i:3d}. {RED}crash{RESET} {e}")
            rows.append({**j, "ok": False, "error": f"crash: {e}", "seconds": 0})
            continue
        dt = time.time() - t0
        d = r.to_dict(); d["seconds"] = round(dt, 2); d.update(job_id=j["job_id"])
        rows.append(d)

        co = (r.company_name or j.get("seen_company") or "?")[:24]
        if r.ok:
            col = GRN if r.confidence >= 0.8 else YEL
            print(f"{i:3d}. {col}OK{RESET}   {co:<24} T{r.tier} "
                  f"{r.confidence:.2f} {dt:5.1f}s  {r.board_url[:56]}")
        else:
            print(f"{i:3d}. {RED}FAIL{RESET} {co:<24} {DIM}{r.error}{RESET}")
        time.sleep(delay)
    browser.__exit__(None, None, None)

    out_name = f"data/results_{kind.split(':')[0]}_{off}.json"
    json.dump(rows, open(out_name, "w"), indent=2)
    json.dump(rows, open("data/results.json", "w"), indent=2)

    # ---- report -------------------------------------------------------
    ok = [r for r in rows if r.get("ok")]
    strong = [r for r in ok if r.get("confidence", 0) >= 0.8]
    by_tier = Counter(r.get("tier") for r in ok)
    fails = Counter((r.get("error") or "unknown").split(";")[0][:52]
                    for r in rows if not r.get("ok"))
    times = [r["seconds"] for r in rows if r.get("seconds")]

    print(f"\n{BOLD}{'='*66}{RESET}")
    print(f"{BOLD}RESULTS{RESET}  n={len(rows)}")
    print(f"  Resolved to a job board : {len(ok)}/{len(rows)}  "
          f"({100*len(ok)/len(rows):.0f}%)")
    print(f"  High confidence (>=0.80): {len(strong)}/{len(rows)}  "
          f"({100*len(strong)/len(rows):.0f}%)")
    print(f"\n{BOLD}Which tier solved it{RESET}")
    for t in sorted(k for k in by_tier if k is not None):
        tier_times = [r["seconds"] for r in ok if r.get("tier") == t]
        avg = sum(tier_times)/len(tier_times) if tier_times else 0
        print(f"  Tier {t}: {by_tier[t]:2d}   avg {avg:.1f}s")
    if times:
        print(f"\n  median {sorted(times)[len(times)//2]:.1f}s   "
              f"total {sum(times):.0f}s")
    if fails:
        print(f"\n{BOLD}Failure modes{RESET}")
        for reason, c in fails.most_common():
            print(f"  {c:2d}x  {reason}")
    print(f"\n{DIM}full results -> {out_name}{RESET}")


if __name__ == "__main__":
    main()
