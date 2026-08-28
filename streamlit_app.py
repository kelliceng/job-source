"""Streamlit Community Cloud entry point.

Free, no card, and enough memory for Chromium -- which is why this exists
alongside the FastAPI app (better API, needs Docker) and the Gradio app
(Hugging Face, whose free tier turned out not to be available).

Same resolver underneath in all three. Only the UI differs.
"""
import os
import subprocess
import sys
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jobsource.agent import default_picker          # noqa: E402
from jobsource.resolver import resolve              # noqa: E402

st.set_page_config(page_title="Job Source Agent", page_icon="🎯",
                   layout="centered")

# Streamlit Cloud supplies secrets via st.secrets. Our modules read plain
# environment variables (so the CLI, the API and the tests all work the same
# way), so bridge them across rather than special-casing the deployment.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:      # no secrets.toml locally -- .env already loaded
    pass


@st.cache_resource(show_spinner=False)
def browser_ready() -> bool:
    """Install Chromium once per container. Cached so it runs on cold start
    only, not per request."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-dev-shm-usage"]).close()
        return True
    except Exception:
        pass
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       check=True, timeout=900,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-dev-shm-usage"]).close()
        return True
    except Exception as e:                                  # noqa: BLE001
        print(f"[startup] Chromium unavailable, Tier 2 off: {e}")
        return False


st.title("Job Source Agent")
st.caption("LinkedIn job posting → the company's own job listing page.")

with st.expander("How it works", expanded=False):
    st.markdown(
        "Four tiers, cheapest first — each one only runs if the previous "
        "failed:\n\n"
        "0. **Guess the ATS account** from the company name, then confirm it "
        "against that platform's public API. ~0.5s, free.\n"
        "1. **Crawl the company site** for a careers link or an embedded job "
        "board.\n"
        "2. **Drive a real browser**, with rules (and an LLM where rules give "
        "up) choosing where to click — for JavaScript-rendered sites.\n"
        "3. **Web search** — the only route to Workday, whose tenant names "
        "can't be guessed.\n\n"
        "Every answer carries **evidence** and a **confidence score**. The "
        "strongest signal is finding the exact job you started from on the "
        "board we returned — that proves it's the right company, not just a "
        "real account with a similar name."
    )

tier2 = browser_ready()
if not tier2:
    st.warning("Browser agent unavailable in this environment — "
               "running tiers 0, 1 and 3 only.", icon="⚠️")

url = st.text_input(
    "LinkedIn job URL",
    placeholder="https://www.linkedin.com/jobs/view/4427787182/")

if st.button("Resolve", type="primary") or url:
    if url.strip():
        picker, kind = default_picker()
        box = st.empty()
        with st.spinner(f"Working through the tiers… (picker: {kind})"):
            t0 = time.time()
            try:
                r = resolve(url.strip(), use_tier2=tier2, picker=picker)
            except Exception as e:                          # noqa: BLE001
                st.error(f"{type(e).__name__}: {e}")
                st.stop()
            dt = time.time() - t0

        if r.ok:
            st.success(f"**[{r.board_url}]({r.board_url})**")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Tier", r.tier)
            c2.metric("Confidence", f"{r.confidence:.2f}")
            c3.metric("Open jobs", r.job_count or "—")
            c4.metric("Time", f"{dt:.1f}s")
            st.markdown("**Evidence**")
            for e in r.evidence:
                st.markdown(f"- {e}")
        else:
            st.error(f"No confident answer — `{r.error}`  ·  {dt:.1f}s")

        with st.expander("How it got there"):
            st.code("\n".join(r.trace), language=None)
