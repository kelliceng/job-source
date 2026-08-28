"""Hugging Face Space entry point.

HF's free tier offers Gradio (Python) but not Docker, so this replaces the
FastAPI front end there. Same resolver underneath -- only the UI differs.

Two things this has to handle that a Docker deploy wouldn't:
  1. Chromium isn't preinstalled, so we fetch it on first boot.
  2. If that fails (sandboxed runner, no disk), Tier 2 is disabled rather than
     letting every request crash -- tiers 0/1/3 still answer.
"""
import os
import subprocess
import sys
import time

import gradio as gr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jobsource.agent import default_picker          # noqa: E402
from jobsource.resolver import resolve              # noqa: E402

TIER2 = True


def ensure_browser() -> bool:
    """Install Chromium once at startup. HF caches it between restarts."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:
        pass
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install",
                        "--with-deps", "chromium"],
                       check=True, timeout=600,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception as e:                                  # noqa: BLE001
        print(f"[startup] Chromium unavailable, disabling Tier 2: {e}")
        return False


def run(url: str):
    url = (url or "").strip()
    if not url:
        return "Paste a LinkedIn job URL first.", ""
    t0 = time.time()
    picker, kind = default_picker()
    try:
        r = resolve(url, use_tier2=TIER2, picker=picker)
    except Exception as e:                                  # noqa: BLE001
        return f"### Error\n\n`{e}`", ""

    dt = time.time() - t0
    trace = "\n".join(r.trace)
    if not r.ok:
        return (f"### No confident answer\n\n"
                f"`{r.error}`\n\n_{dt:.1f}s · picker: {kind}_"), trace

    ev = "\n".join(f"- {e}" for e in r.evidence) or "_none_"
    return (
        f"### [{r.board_url}]({r.board_url})\n\n"
        f"**Tier {r.tier}** · confidence **{r.confidence:.2f}**"
        + (f" · **{r.job_count}** open jobs" if r.job_count else "")
        + f" · {dt:.1f}s\n\n"
        f"**Evidence**\n{ev}"
    ), trace


with gr.Blocks(title="Job Source Agent", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        "# Job Source Agent\n"
        "LinkedIn job posting &rarr; the company's own job listing page.\n\n"
        "Four tiers, cheapest first: guess the ATS account and confirm it "
        "against that platform's public API; crawl the company site; drive a "
        "real browser with an LLM picking where to click; web search. "
        "Every answer carries evidence and a confidence score."
    )
    with gr.Row():
        inp = gr.Textbox(
            label="LinkedIn job URL", scale=4, max_lines=1,
            placeholder="https://www.linkedin.com/jobs/view/4427787182/")
        btn = gr.Button("Resolve", variant="primary", scale=1)
    out = gr.Markdown()
    with gr.Accordion("How it got there", open=False):
        tr = gr.Code(label="", language=None)

    gr.Examples(
        examples=[["https://www.linkedin.com/jobs/view/4427787182/"]],
        inputs=inp, label="Try one")

    btn.click(run, inputs=inp, outputs=[out, tr])
    inp.submit(run, inputs=inp, outputs=[out, tr])

if __name__ == "__main__":
    TIER2 = ensure_browser()
    print(f"[startup] Tier 2 (browser agent): "
          f"{'enabled' if TIER2 else 'DISABLED'}")
    demo.queue(max_size=12).launch(server_name="0.0.0.0",
                                   server_port=int(os.environ.get("PORT", 7860)))
