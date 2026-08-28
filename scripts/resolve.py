#!/usr/bin/env python3
"""CLI: python scripts/resolve.py <linkedin job url> [...]"""
import json
import sys
import time
sys.path.insert(0, __file__.rsplit("/", 2)[0])

from jobsource.resolver import resolve


def main() -> None:
    urls = [a for a in sys.argv[1:] if not a.startswith("-")]
    as_json = "--json" in sys.argv
    if not urls:
        print(__doc__)
        sys.exit(1)

    for url in urls:
        t0 = time.time()
        if not as_json:
            print(f"\n\033[1m{url}\033[0m")
        r = resolve(url, verbose=not as_json)
        dt = time.time() - t0
        if as_json:
            print(json.dumps({**r.to_dict(), "seconds": round(dt, 2)}, indent=2))
        else:
            if r.ok:
                print(f"\n  \033[32m=> {r.board_url}\033[0m")
                print(f"     tier {r.tier} | confidence {r.confidence:.2f} | "
                      f"{r.job_count or '?'} jobs | {dt:.2f}s")
                for e in r.evidence:
                    print(f"     - {e}")
            else:
                print(f"\n  \033[31m=> no answer\033[0m ({r.error}) | {dt:.2f}s")


if __name__ == "__main__":
    main()
