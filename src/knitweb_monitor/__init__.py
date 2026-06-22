"""knitweb-monitor — a zero-dependency, cross-platform dashboard for Knitweb nodes,
the woven knowledge graph, and live MOLGANG game sessions.

Pure Python (stdlib only). Runs identically on Windows, macOS and Linux:

    python -m knitweb_monitor --molgang http://localhost:8765
    knitweb-monitor --port 8990 --node alice=~/.knode/alice.json:8900 --open

Optional, auto-detected, never required:
  * **networkx** — nicer graph layout (a pure-Python force-directed fallback is used otherwise)
  * **knitweb**  — read node wallet ledger state (balances/transfers); without it the node
    panel still shows liveness, and the MOLGANG + knowledge-graph views work fully over HTTP.

It is **read-only**: it polls HTTP endpoints and reads wallet snapshots; it never writes
state, moves funds, or talks a node's wire protocol.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.1.0"

# Optional deps — detected once, never required.
try:
    import networkx as _nx
except Exception:
    _nx = None


# --------------------------------------------------------------------------- config
class Config:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 8990
        self.nodes: list[dict] = []          # [{label, wallet, port}]
        self.molgang: list[str] = []         # base URLs of MOLGANG sessions
        self.symbol = "PLS"
        self.knitweb_src: str | None = None
        self.active_window = 30              # seconds a port-beat counts as "live"


CFG = Config()


# --------------------------------------------------------------------------- helpers
def port_live(port: int | None, host: str = "127.0.0.1") -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=0.4):
            return True
    except OSError:
        return False


# Short-TTL cache so a single poll tick (build_graph calls _game_links while read_molgang already
# fetched /api/web) and concurrent browser polls (2s/3s/20s intervals) share ONE blocking fetch per
# URL instead of fanning out duplicates. TTL < the 2s poll interval keeps the dashboard fresh. The
# URL set is fixed by config (nodes + molgang), so the cache stays tiny — no eviction needed.
_HTTP_CACHE: dict = {}
_HTTP_CACHE_LOCK = threading.Lock()
_HTTP_CACHE_URL_LOCKS: dict[str, threading.Lock] = {}
_HTTP_CACHE_TTL = 1.5  # seconds


def _http_cache_lock(url: str) -> threading.Lock:
    with _HTTP_CACHE_LOCK:
        lock = _HTTP_CACHE_URL_LOCKS.get(url)
        if lock is None:
            lock = threading.Lock()
            _HTTP_CACHE_URL_LOCKS[url] = lock
        return lock


def http_json(url: str, timeout: float = 2.5):
    now = time.monotonic()
    with _HTTP_CACHE_LOCK:
        hit = _HTTP_CACHE.get(url)
        if hit is not None and now - hit[0] < _HTTP_CACHE_TTL:
            return hit[1]

    with _http_cache_lock(url):
        now = time.monotonic()
        with _HTTP_CACHE_LOCK:
            hit = _HTTP_CACHE.get(url)
            if hit is not None and now - hit[0] < _HTTP_CACHE_TTL:
                return hit[1]
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            data = None
        with _HTTP_CACHE_LOCK:
            _HTTP_CACHE[url] = (time.monotonic(), data)
        return data


def _load_knitweb():
    """Import knitweb if available (optionally from a configured src path)."""
    if CFG.knitweb_src and CFG.knitweb_src not in sys.path:
        sys.path.insert(0, CFG.knitweb_src)
    try:
        from knitweb import store  # noqa: F401
        return store
    except Exception:
        return None


# --------------------------------------------------------------------------- nodes
def read_nodes() -> dict:
    store = _load_knitweb()
    out = {"knitweb": store is not None, "nodes": []}
    for n in CFG.nodes:
        live = port_live(n.get("port"))
        entry = {"label": n["label"], "port": n.get("port"), "live": live, "ok": False}
        wallet = n.get("wallet")
        if store is not None and wallet and os.path.isfile(os.path.expanduser(wallet)):
            try:
                node = store.load_node(os.path.expanduser(wallet))
                transfers, prev = [], 0
                for f in node.braid.fibers:
                    bal = f.balance(CFG.symbol)
                    if f.seq == 0:
                        prev = bal
                        continue
                    transfers.append({"seq": f.seq, "delta": bal - prev,
                                      "knit": (f.knit or "")[:18] + ("…" if f.knit and len(f.knit) > 18 else "")})
                    prev = bal
                entry.update({"ok": True, "address": node.address, "balance": node.balance(CFG.symbol),
                              "seq": node.braid.head.seq, "nonce": node.nonce,
                              "n_transfers": len(transfers), "transfers": transfers[-25:]})
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {e}"
        elif wallet and store is None:
            entry["error"] = "install knitweb to read ledger state"
        out["nodes"].append(entry)
    return out


# --------------------------------------------------------------------------- molgang
def read_molgang() -> dict:
    sessions = []
    for url in CFG.molgang:
        base = url.rstrip("/")
        st = http_json(base + "/api/state")
        web = http_json(base + "/api/web")
        sessions.append({"url": base, "live": st is not None, "state": st, "web": web})
    return {"active": any(s["live"] for s in sessions), "sessions": sessions, "urls": CFG.molgang}


# --------------------------------------------------------------------------- graph
def _game_links() -> list:
    links = []
    for url in CFG.molgang:
        web = http_json(url.rstrip("/") + "/api/web")
        if isinstance(web, dict):
            anchored = bool((web.get("anchor") or {}).get("verified"))
            for lk in web.get("links", []) or []:
                if all(k in lk for k in ("subject", "relation", "object")):
                    links.append({**lk, "anchored": anchored})
    return links


def _layout(nodes: list[str], edges: list[tuple[str, str]]) -> dict:
    """Return {node: (x, y)} in [-1, 1]. Uses networkx if present, else a pure-Python
    deterministic Fruchterman-Reingold so the monitor needs zero dependencies."""
    if not nodes:
        return {}
    if _nx is not None:
        g = _nx.DiGraph()
        g.add_nodes_from(nodes)
        g.add_edges_from(edges)
        pos = _nx.spring_layout(g, seed=7, iterations=80, k=1.1)
        return {n: (round(float(p[0]), 4), round(float(p[1]), 4)) for n, p in pos.items()}
    # --- pure-Python fallback: deterministic force-directed layout ---
    import math
    import random
    rnd = random.Random(7)
    pos = {n: [rnd.uniform(-1, 1), rnd.uniform(-1, 1)] for n in nodes}
    k = 0.6
    adj = [(a, b) for a, b in edges if a in pos and b in pos]
    for it in range(120):
        disp = {n: [0.0, 0.0] for n in nodes}
        for i, a in enumerate(nodes):                       # repulsion
            for b in nodes[i + 1:]:
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                d2 = dx * dx + dy * dy + 1e-6
                f = (k * k) / d2
                disp[a][0] += dx * f; disp[a][1] += dy * f
                disp[b][0] -= dx * f; disp[b][1] -= dy * f
        for a, b in adj:                                    # attraction
            dx = pos[a][0] - pos[b][0]
            dy = pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) + 1e-6
            f = (d * d) / k
            disp[a][0] -= dx / d * f; disp[a][1] -= dy / d * f
            disp[b][0] += dx / d * f; disp[b][1] += dy / d * f
        t = 0.1 * (1 - it / 120)
        for n in nodes:
            dx, dy = disp[n]
            d = math.hypot(dx, dy) + 1e-6
            pos[n][0] += dx / d * min(d, t)
            pos[n][1] += dy / d * min(d, t)
    mx = max((abs(p[0]) for p in pos.values()), default=1) or 1
    my = max((abs(p[1]) for p in pos.values()), default=1) or 1
    return {n: (round(p[0] / mx, 4), round(p[1] / my, 4)) for n, p in pos.items()}


def build_graph() -> dict:
    g_nodes: dict[str, dict] = {}
    g_edges: list[dict] = []
    edge_pairs: list[tuple[str, str]] = []

    # ledger layer (live, optional — needs knitweb)
    nd = read_nodes()
    legs: dict[str, list] = {}
    for n in nd["nodes"]:
        if n.get("ok"):
            g_nodes[n["label"]] = {"layer": "ledger", "label": f"{n['label']}\n{n['balance']} {CFG.symbol}"}
            for t in n.get("transfers", []):
                legs.setdefault(t["knit"], []).append((n["label"], t["delta"]))
    for knit, ls in legs.items():
        payer = next((l for l, d in ls if d < 0), None)
        payee = next((l for l, d in ls if d > 0), None)
        if payer and payee:
            amt = abs(next(d for l, d in ls if l == payer))
            g_edges.append({"src": payer, "dst": payee, "rel": f"{amt} {CFG.symbol}", "layer": "ledger"})
            edge_pairs.append((payer, payee))

    # knowledge layer (live MOLGANG fabric, over HTTP — no deps)
    for lk in _game_links():
        s, o = str(lk["subject"]), str(lk["object"])
        g_nodes.setdefault(s, {"layer": "knowledge", "label": s})
        g_nodes.setdefault(o, {"layer": "knowledge", "label": o[:24]})
        g_edges.append({"src": s, "dst": o, "rel": str(lk["relation"]) + (" ⚓" if lk.get("anchored") else ""),
                        "layer": "knowledge"})
        edge_pairs.append((s, o))

    names = list(g_nodes.keys())
    pos = _layout(names, edge_pairs)
    out_nodes = [{"id": n, "label": d["label"], "layer": d["layer"],
                  "x": pos.get(n, (0, 0))[0], "y": pos.get(n, (0, 0))[1]} for n, d in g_nodes.items()]
    return {"nodes": out_nodes, "edges": g_edges,
            "stats": {"nodes": len(out_nodes), "edges": len(g_edges),
                      "layout": "networkx" if _nx is not None else "builtin",
                      "knitweb": nd["knitweb"]}}


# --------------------------------------------------------------------------- HTML
def _page() -> str:
    return PAGE


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Knitweb monitor</title><style>
 body{font:14px/1.55 ui-monospace,Menlo,Consolas,monospace;background:#0b0f1a;color:#e7edf5;margin:0;padding:20px}
 h1{font-size:18px;margin:0 0 4px}.sub{color:#8b98ad;margin:0 0 12px}
 .tabs{display:flex;gap:6px;border-bottom:1px solid #28324a;margin-bottom:16px}
 .tab{padding:8px 18px;cursor:pointer;border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0;color:#8b98ad}
 .tab.on{background:#141a28;border-color:#28324a;color:#5aa0ff;font-weight:700}
 .grid{display:flex;gap:16px;flex-wrap:wrap}
 .card{background:#141a28;border:1px solid #28324a;border-radius:12px;padding:16px;min-width:300px;flex:1}
 .bal{font-size:28px;font-weight:700;color:#5aa0ff}.unit{font-size:13px;color:#8b98ad}
 .row{display:flex;justify-content:space-between;border-top:1px solid #20283c;padding:3px 0;gap:10px}
 .pos{color:#39d98a}.neg{color:#ff6f87}.muted{color:#8b98ad}
 .feed{max-height:200px;overflow:auto;margin-top:8px;font-size:12px}
 .dot{height:8px;width:8px;border-radius:50%;display:inline-block;margin-right:6px}.up{background:#39d98a}.down{background:#ff6f87}
 #kg{width:100%;height:460px;background:#0b0f1a;border-radius:8px}
 .lg{display:inline-block;margin-right:14px}.sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle}
 .pane{display:none}.pane.on{display:block}.badge{font-size:11px;padding:2px 8px;border-radius:10px}
 .table{border:1px solid #28324a;border-radius:8px;padding:10px;margin:8px 0}
 .chip{display:inline-block;background:#20283c;border-radius:8px;padding:1px 7px;margin:2px;font-size:12px}
 a{color:#5aa0ff}
</style></head><body>
<h1>🕸️ Knitweb monitor <span class="muted" id="ver"></span></h1>
<p class="sub"><span id="ts"></span></p>
<div class="tabs">
 <div class="tab on" data-t="nodes" onclick="sw('nodes')">Nodes</div>
 <div class="tab" data-t="graph" onclick="sw('graph')">🧠 Knowledge graph</div>
 <div class="tab" data-t="molgang" onclick="sw('molgang')">🧪 MOLGANG</div>
</div>
<div id="pane-nodes" class="pane on"><div class="grid" id="nodes"></div></div>
<div id="pane-graph" class="pane"><div class="card"><h2 style="margin:0">🧠 Knowledge graph <span class="muted" id="kgstat"></span></h2>
 <p class="muted" style="margin:4px 0">refresh 20s · <span class="lg"><span class="sw" style="background:#5aa0ff"></span>ledger</span>
  <span class="lg"><span class="sw" style="background:#a98bff"></span>knowledge (MOLGANG fabric)</span></p>
 <svg id="kg" viewBox="0 0 1000 460" preserveAspectRatio="xMidYMid meet"></svg></div></div>
<div id="pane-molgang" class="pane"><div class="grid" id="mol"></div></div>
<script>
const NS="http://www.w3.org/2000/svg",$=id=>document.getElementById(id);
const fmt=n=>(n||0).toLocaleString(),esc=s=>(s==null?'':String(s)).replace(/</g,'&lt;');
function sw(t){document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('on',e.dataset.t===t));document.querySelectorAll('.pane').forEach(e=>e.classList.toggle('on',e.id==='pane-'+t));}
async function jget(u){try{return await(await fetch(u)).json()}catch(e){return null}}
async function tickNodes(){const d=await jget('/api/nodes');if(!d)return;$('ts').textContent='updated '+new Date().toLocaleTimeString();
 const box=$('nodes');box.innerHTML='';
 if(!d.nodes.length){box.innerHTML='<div class="card muted">No nodes configured. Add <code>--node label=wallet.json:port</code>.'+(d.knitweb?'':' (install <code>knitweb</code> to read ledger state)')+'</div>';return;}
 d.nodes.forEach(n=>{const c=document.createElement('div');c.className='card';
  const dot=n.live?'<span class="dot up"></span>':'<span class="dot down"></span>';
  const stat=n.port?(n.live?`<span class="pos" style="font-size:11px">● live :${n.port}</span>`:`<span class="neg" style="font-size:11px">● down :${n.port}</span>`):'';
  if(!n.ok){c.innerHTML=`<h2>${dot}${esc(n.label)} ${stat}</h2><p class="muted">${esc(n.error||'no ledger data')}</p>`;box.appendChild(c);return;}
  const feed=(n.transfers||[]).slice().reverse().map(t=>`<div class="row"><span class="muted">#${t.seq} ${t.knit}</span><span class="${t.delta>=0?'pos':'neg'}">${t.delta>=0?'+':''}${fmt(t.delta)}</span></div>`).join('')||'<p class="muted">no transfers</p>';
  c.innerHTML=`<h2>${dot}${esc(n.label)} ${stat}</h2><div class="bal">${fmt(n.balance)} <span class="unit">PLS</span></div>
   <div class="row"><span class="muted">address</span><span>${esc(n.address)}</span></div>
   <div class="row"><span class="muted">seq / nonce</span><span>${n.seq} / ${n.nonce}</span></div>
   <div class="row"><span class="muted">transfers</span><span>${n.n_transfers}</span></div><div class="feed">${feed}</div>`;box.appendChild(c);});}
function drawGraph(g){const svg=$('kg');while(svg.firstChild)svg.removeChild(svg.firstChild);if(!g.nodes.length)return;
 const W=1000,H=460,pad=70,xs=g.nodes.map(n=>n.x),ys=g.nodes.map(n=>n.y);
 const mnx=Math.min(...xs),mxx=Math.max(...xs),mny=Math.min(...ys),mxy=Math.max(...ys);
 const sx=x=>pad+(mxx-mnx?(x-mnx)/(mxx-mnx):.5)*(W-2*pad),sy=y=>pad+(mxy-mny?(y-mny)/(mxy-mny):.5)*(H-2*pad);
 const P={};g.nodes.forEach(n=>P[n.id]=[sx(n.x),sy(n.y)]);
 g.edges.forEach(e=>{const a=P[e.src],b=P[e.dst];if(!a||!b)return;const col=e.layer==='ledger'?'#5aa0ff':'#7a5cff';
  const l=document.createElementNS(NS,'line');l.setAttribute('x1',a[0]);l.setAttribute('y1',a[1]);l.setAttribute('x2',b[0]);l.setAttribute('y2',b[1]);l.setAttribute('stroke',col);l.setAttribute('stroke-width',e.layer==='ledger'?2.2:1.4);l.setAttribute('opacity','.6');svg.appendChild(l);
  const t=document.createElementNS(NS,'text');t.setAttribute('x',(a[0]+b[0])/2);t.setAttribute('y',(a[1]+b[1])/2-3);t.setAttribute('fill','#8b98ad');t.setAttribute('font-size','10');t.setAttribute('text-anchor','middle');t.textContent=e.rel;svg.appendChild(t);});
 g.nodes.forEach(n=>{const p=P[n.id],led=n.layer==='ledger';const c=document.createElementNS(NS,'circle');
  c.setAttribute('cx',p[0]);c.setAttribute('cy',p[1]);c.setAttribute('r',led?13:8);c.setAttribute('fill',led?'#2f6fed':'#7a5cff');c.setAttribute('stroke','#0b0f1a');c.setAttribute('stroke-width','2');svg.appendChild(c);
  const t=document.createElementNS(NS,'text');t.setAttribute('x',p[0]);t.setAttribute('y',p[1]-(led?18:12));t.setAttribute('fill','#e7edf5');t.setAttribute('font-size',led?'12':'10');t.setAttribute('text-anchor','middle');t.textContent=(n.label||n.id).split('\n')[0];svg.appendChild(t);
  if(led){const b=document.createElementNS(NS,'text');b.setAttribute('x',p[0]);b.setAttribute('y',p[1]+4);b.setAttribute('fill','#fff');b.setAttribute('font-size','9');b.setAttribute('text-anchor','middle');b.textContent=(n.label||'').split('\n')[1]||'';svg.appendChild(b);}});}
async function tickGraph(){const g=await jget('/api/graph');if(!g)return;$('kgstat').textContent=`— ${g.stats.nodes} nodes / ${g.stats.edges} edges · layout: ${g.stats.layout}`;drawGraph(g);}
async function tickMol(){const m=await jget('/api/molgang');if(!m)return;const box=$('mol');
 if(!m.urls.length){box.innerHTML='<div class="card muted">No MOLGANG sessions configured. Add <code>--molgang http://localhost:8765</code>.</div>';return;}
 box.innerHTML=m.sessions.map(s=>{const hdr=`<h2 style="margin:0">🍸 ${esc(s.url)} ${s.live?'<span class="badge" style="background:#15402a;color:#39d98a">● live</span>':'<span class="badge" style="background:#3a2a08;color:#d6a429">● down</span>'} <a href="${esc(s.url)}" target="_blank" style="font-size:12px">open ↗</a></h2>`;
  if(!s.live||!s.state)return `<div class="card">${hdr}<p class="muted">not responding</p></div>`;
  const st=s.state,web=s.web||{};let h=hdr;
  if(web.anchor)h+=`<p class="muted">fabric ${web.nodes||0}n/${web.edges||0}e · OriginTrail ${web.anchor.verified?'<span class="pos">✓ verified</span>':'unverified'}</p>`;
  (st.tables||[]).forEach(tb=>{h+=`<div class="table"><b>${esc(tb.name)}</b> <span class="muted">(${(tb.seated||[]).length}/${tb.seats})</span><div>`;
   (tb.seated||[]).forEach(p=>{h+=`<span class="chip">${esc(p.name)} <span class="muted">L${p.level} · ${p.woven}🧬</span></span>`;});h+='</div>';
   (tb.fabric||[]).forEach(f=>{h+=`<div class="row"><span>🧵 ${esc(f.term)}</span><span class="muted">${f.confirmations}✓</span></div>`;});h+='</div>';});
  return `<div class="card" style="flex:1 1 100%">${h}</div>`;}).join('');}
function all(){tickNodes();tickGraph();tickMol();}
jget('/api/health').then(h=>{if(h)$('ver').textContent='v'+h.version});
sw('nodes');all();setInterval(tickNodes,2000);setInterval(tickMol,3000);setInterval(tickGraph,20000);
</script></body></html>"""


# --------------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path.startswith("/api/nodes"):
                self._send(json.dumps(read_nodes()).encode(), "application/json")
            elif self.path.startswith("/api/graph"):
                self._send(json.dumps(build_graph()).encode(), "application/json")
            elif self.path.startswith("/api/molgang"):
                self._send(json.dumps(read_molgang()).encode(), "application/json")
            elif self.path.startswith("/api/health"):
                self._send(json.dumps({"ok": True, "version": __version__,
                                       "networkx": _nx is not None}).encode(), "application/json")
            else:
                self._send(_page().encode(), "text/html; charset=utf-8")
        except BrokenPipeError:
            pass


# --------------------------------------------------------------------------- cli
def _parse_node(spec: str) -> dict:
    # label=wallet.json:port  |  label=wallet.json  |  label::port  |  label
    label, _, rest = spec.partition("=")
    wallet, port = rest, None
    if rest.count(":") and not rest.lower().startswith(("c:", "d:")):  # avoid Windows drive letters
        head, _, tail = rest.rpartition(":")
        if tail.isdigit():
            wallet, port = head, int(tail)
    return {"label": label, "wallet": wallet or None, "port": port}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="knitweb-monitor",
        description="Cross-platform dashboard for Knitweb nodes, the knowledge graph, and MOLGANG.")
    ap.add_argument("--host", default=os.environ.get("KNITWEB_MONITOR_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("KNITWEB_MONITOR_PORT", "8990")))
    ap.add_argument("--node", action="append", default=[], metavar="LABEL=WALLET[:PORT]",
                    help="a knitweb node wallet to watch (repeatable)")
    ap.add_argument("--molgang", action="append", default=[], metavar="URL",
                    help="a MOLGANG session base URL, e.g. http://localhost:8765 (repeatable)")
    ap.add_argument("--knitweb-src", default=os.environ.get("KNITWEB_SRC"),
                    help="path to a knitweb 'src' checkout, if knitweb isn't installed")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    ap.add_argument("--version", action="version", version=f"knitweb-monitor {__version__}")
    args = ap.parse_args(argv)

    CFG.host, CFG.port, CFG.knitweb_src = args.host, args.port, args.knitweb_src
    CFG.nodes = [_parse_node(s) for s in args.node]
    CFG.molgang = list(args.molgang) or [u for u in (os.environ.get("KNITWEB_MONITOR_MOLGANG", "").split(",")) if u]

    srv = ThreadingHTTPServer((CFG.host, CFG.port), Handler)
    url = f"http://{CFG.host}:{CFG.port}"
    print(f"knitweb-monitor {__version__} → {url}")
    print(f"  nodes: {len(CFG.nodes)} · molgang: {len(CFG.molgang)} · "
          f"networkx: {'yes' if _nx else 'no (builtin layout)'} · knitweb: {'yes' if _load_knitweb() else 'no'}")
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0
