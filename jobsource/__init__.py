"""Job source agent: LinkedIn job URL -> the company's own job listing page.

Loading .env here (rather than in each entry point) means the CLI, the
evaluation harness and the web app all pick up API keys the same way, with no
per-script boilerplate and nothing secret on the command line.
"""
import os as _os

try:
    from dotenv import load_dotenv as _load

    # Walk up from this file to the project root and load a .env if present.
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    _load(_os.path.join(_root, ".env"))
except ImportError:      # python-dotenv is optional; env vars still work
    pass
