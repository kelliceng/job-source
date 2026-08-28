"""Web front end: paste a LinkedIn job URL, watch the ladder work.

The streaming trace is the point. Seeing Tier 0 land in 400ms -- and seeing the
browser agent take over when it misses -- explains the architecture without
anyone narrating it.
"""
import asyncio
import json
import os
import sys

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobsource.resolver import resolve  # noqa: E402

app = FastAPI(title="Job Source Agent")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/resolve")
async def api_resolve(url: str):
    """Non-streaming JSON, for scripted testing."""
    r = await asyncio.to_thread(resolve, url)
    return r.to_dict()


@app.get("/api/stream")
async def api_stream(url: str):
    """Server-sent events: one `step` per trace line, then a final `result`."""
    async def gen():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        class Tee(list):
            """resolve() appends trace lines to res.trace; mirror them out."""
            def append(self, item):
                super().append(item)
                loop.call_soon_threadsafe(queue.put_nowait, ("step", item))

        def work():
            from jobsource.models import Resolution
            orig = Resolution.__init__

            def patched(self, *a, **kw):
                orig(self, *a, **kw)
                self.trace = Tee()
            Resolution.__init__ = patched
            try:
                return resolve(url)
            finally:
                Resolution.__init__ = orig

        task = loop.run_in_executor(None, work)
        while True:
            done, _ = await asyncio.wait(
                [asyncio.ensure_future(queue.get()), task],
                return_when=asyncio.FIRST_COMPLETED, timeout=90)
            if task.done():
                while not queue.empty():
                    kind, payload = queue.get_nowait()
                    yield f"event: step\ndata: {json.dumps(payload)}\n\n"
                res = task.result()
                yield f"event: result\ndata: {json.dumps(res.to_dict())}\n\n"
                return
            for d in done:
                try:
                    kind, payload = d.result()
                    yield f"event: step\ndata: {json.dumps(payload)}\n\n"
                except Exception:
                    pass

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX


INDEX = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Job Source Agent</title><style>
:root{--bg:#f5f7f8;--fg:#0f161c;--dim:#5b6771;--line:#d9e0e4;--card:#fff;--acc:#0c6e79}
@media(prefers-color-scheme:dark){:root{--bg:#0c1116;--fg:#e3e9ed;--dim:#98a6b2;--line:#243039;--card:#131b22;--acc:#43b3bf}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:16px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif;margin:0;padding:40px 20px}
.w{max-width:760px;margin:0 auto}
h1{font-size:1.6rem;margin:0 0 6px;letter-spacing:-.02em}
p.sub{color:var(--dim);margin:0 0 26px}
form{display:flex;gap:8px;flex-wrap:wrap}
input{flex:1 1 340px;min-width:0;padding:11px 13px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);font:inherit;font-size:.92rem}
button{padding:11px 20px;border:0;border-radius:6px;background:var(--acc);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
#trace{margin-top:24px;font-family:ui-monospace,Menlo,monospace;font-size:.79rem;line-height:1.75;color:var(--dim);white-space:pre-wrap;word-break:break-word}
#out{margin-top:20px;padding:18px;border:1px solid var(--line);border-radius:8px;background:var(--card);display:none}
#out.on{display:block}
#out a{color:var(--acc);font-weight:600;font-size:1.02rem;word-break:break-all}
.meta{color:var(--dim);font-size:.83rem;margin-top:8px;font-family:ui-monospace,Menlo,monospace}
.ev{margin-top:10px;font-size:.85rem;color:var(--dim)}
.ev div{margin-top:3px}
.ex{margin-top:18px;font-size:.82rem;color:var(--dim)}
.ex a{color:var(--acc);cursor:pointer}
</style></head><body><div class=w>
<h1>Job Source Agent</h1>
<p class=sub>LinkedIn job posting &rarr; the company's own job listing page.</p>
<form id=f>
  <input id=u placeholder="https://www.linkedin.com/jobs/view/4427787182/" autocomplete=off>
  <button id=b>Resolve</button>
</form>
<div class=ex>Try: <a data-u="https://www.linkedin.com/jobs/view/4427787182/">Harvey (tier 0)</a></div>
<div id=trace></div>
<div id=out></div>
</div><script>
const f=document.getElementById('f'),u=document.getElementById('u'),
      b=document.getElementById('b'),tr=document.getElementById('trace'),
      out=document.getElementById('out');
document.querySelectorAll('.ex a').forEach(a=>a.onclick=()=>{u.value=a.dataset.u;f.requestSubmit()});
f.onsubmit=e=>{
  e.preventDefault();
  if(!u.value.trim())return;
  tr.textContent='';out.className='';out.innerHTML='';b.disabled=true;b.textContent='Working...';
  const es=new EventSource('/api/stream?url='+encodeURIComponent(u.value.trim()));
  es.addEventListener('step',ev=>{tr.textContent+=JSON.parse(ev.data)+'\\n';window.scrollTo(0,document.body.scrollHeight)});
  es.addEventListener('result',ev=>{
    const r=JSON.parse(ev.data);es.close();b.disabled=false;b.textContent='Resolve';
    out.className='on';
    if(r.ok){
      out.innerHTML='<a href="'+r.board_url+'" target="_blank" rel="noopener">'+r.board_url+'</a>'+
        '<div class=meta>tier '+r.tier+' &middot; confidence '+r.confidence.toFixed(2)+
        (r.job_count?' &middot; '+r.job_count+' open jobs':'')+'</div>'+
        '<div class=ev>'+r.evidence.map(x=>'<div>&bull; '+x+'</div>').join('')+'</div>';
    } else {
      out.innerHTML='<b>No confident answer.</b><div class=meta>'+(r.error||'')+'</div>';
    }
  });
  es.onerror=()=>{es.close();b.disabled=false;b.textContent='Resolve';};
};
</script></body></html>"""
