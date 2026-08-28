#!/usr/bin/env python3
"""Verify an LLM backend works before running the full evaluation.

  python scripts/check_llm.py            # which backend is active, list models
  python scripts/check_llm.py --live     # also run one real navigation decision
"""
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from jobsource import agent as A  # noqa: E402

GRN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def main() -> None:
    keys = {
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY"),
        "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEY"),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
    }
    print(f"{BOLD}Keys in environment{RESET}")
    for k, v in keys.items():
        mark = f"{GRN}set{RESET}" if v else f"{DIM}not set{RESET}"
        tail = f" {DIM}(...{v[-4:]}){RESET}" if v else ""
        print(f"  {k:20s} {mark}{tail}")

    if keys["GEMINI_API_KEY"] or keys["GOOGLE_API_KEY"]:
        print(f"\n{BOLD}Gemini models this key can call{RESET}")
        try:
            names = A.available_models()
            flash = [n for n in names if "flash" in n and "live" not in n]
            for n in sorted(flash)[:12]:
                print(f"  {n}")
            if not flash:
                print(f"  {DIM}(no flash models listed; {len(names)} total){RESET}")
            print(f"\n  chosen: {GRN}{A.pick_model()}{RESET}")
        except Exception as e:                              # noqa: BLE001
            print(f"  {RED}failed: {e}{RESET}")
            sys.exit(1)

    picker, kind = A.default_picker()
    print(f"\n{BOLD}Active picker:{RESET} {GRN}{kind}{RESET}")
    if kind == "heuristic":
        print(f"  {DIM}No LLM key found -- Tier 2 will use rules.{RESET}")

    if "--live" not in sys.argv:
        print(f"\n{DIM}Add --live to make one real navigation decision.{RESET}")
        return

    print(f"\n{BOLD}Live test{RESET}  ekimetrics.com "
          f"{DIM}(the case rules fail: link reads 'Join Ekimetrics'){RESET}")
    from jobsource.browser import Browser
    with Browser() as b:
        page = b.visit("https://www.ekimetrics.com")
        if page is None:
            print(f"  {RED}could not load page{RESET}")
            sys.exit(1)
        print(f"  page has {len(page.links)} links")
        rule = A.choose_heuristic(page, set())
        print(f"  {DIM}heuristic:{RESET} {rule.action} {rule.href or ''} "
              f"({rule.reason})")
        try:
            d = picker(page, set())
        except Exception as e:                              # noqa: BLE001
            print(f"  {RED}picker failed: {e}{RESET}")
            sys.exit(1)
        col = GRN if d.action == "click" else RED
        print(f"  {BOLD}{kind}:{RESET} {col}{d.action}{RESET} {d.href or ''}")
        print(f"     reason: {d.reason}")


if __name__ == "__main__":
    main()
