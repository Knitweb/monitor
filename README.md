# knitweb-monitor

A **zero-dependency, cross-platform** dashboard for [Knitweb](https://github.com/knitweb)
nodes, the woven **knowledge graph**, and live **MOLGANG** game sessions.

Pure Python (stdlib only) — runs the same on **Windows, macOS and Linux**. No build step,
no native deps, no framework. One file, `pip`-installable or run straight from source.

![tabs: Nodes · Knowledge graph · MOLGANG](docs/screenshot.png)

## Install & run

```bash
pip install knitweb-monitor          # or: pipx install knitweb-monitor
knitweb-monitor --molgang http://localhost:8765 --open
```

Or with **no install at all** (just Python ≥ 3.9):

```bash
git clone https://github.com/knitweb/monitor && cd monitor
python -m knitweb_monitor --molgang http://localhost:8765
# Windows:  py -m knitweb_monitor --molgang http://localhost:8765
```

Then open <http://127.0.0.1:8990>.

## What it shows

- **Nodes** — for each watched knitweb node: PLS balance, address, seq/nonce, a live transfer
  feed, and daemon liveness (port check). *(Ledger reading needs the optional `knitweb`
  package; without it the panel still shows liveness.)*
- **Knowledge graph** — the woven fabric as a force-directed graph: ledger transfers (blue) +
  the MOLGANG knowledge layer (`subject —relation→ object`, ⚓ = OriginTrail-anchored).
- **MOLGANG** — every configured session's tables, seated players, and woven fabric, with a
  link to open each.

## Options

```
--molgang URL            a MOLGANG session base URL (repeatable), e.g. http://localhost:8765
--node LABEL=WALLET[:PORT]  a knitweb node wallet to watch (repeatable); PORT enables liveness
--port N                 dashboard port (default 8990)
--host HOST              bind address (default 127.0.0.1)
--knitweb-src PATH       path to a knitweb 'src' checkout if knitweb isn't pip-installed
--open                   open the dashboard in your browser
```

Examples:

```bash
# watch two local nodes + two game sessions
knitweb-monitor \
  --node alice=~/.knode/alice.json:8900 \
  --node bob=~/.knode/bob.json:8901 \
  --molgang http://localhost:8765 \
  --molgang http://localhost:9876 --open
```

Everything is configurable by env too: `KNITWEB_MONITOR_PORT`, `KNITWEB_MONITOR_MOLGANG`
(comma-separated URLs), `KNITWEB_SRC`.

## Optional extras (auto-detected, never required)

| Extra | Effect when present | Without it |
|---|---|---|
| `networkx` (`pip install knitweb-monitor[graph]`) | nicer graph layout | a built-in pure-Python force-directed layout |
| `knitweb` (`pip install knitweb-monitor[knitweb]`) | reads node ledger state | node panel shows liveness only; MOLGANG + graph work fully |

## Safety

Read-only. It polls HTTP endpoints and reads wallet snapshots — it never writes state, moves
funds, or speaks a node's wire protocol. Binds to `127.0.0.1` by default.

## License
Apache-2.0.
