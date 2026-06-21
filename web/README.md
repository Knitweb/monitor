# web/ — MONITOR browser assets

## `knitweb-graph-3d.html`
A self-contained, dependency-free **3D knowledge-graph explorer** for the knitweb (Three.js + d3-force-3d, libraries inlined — no CDN/internet needed). Three views via the top-left control:

- **Plugins** — the 21 domain-plugins (knitwebs) coupled via vBank governance voting.
- **Begrippen** — the 10 core concepts as a relation matrix (hover a link for the 2-word relation).
- **Live chem-web** — fetches `explorer-graph.json` (same-origin; falls back to `/molgang/explorer-graph.json` then `https://5mart.ml/molgang/explorer-graph.json`) and renders the live molgang chemistry knowledge web in 3D.

Interactions: hover a node for its keywords / definition (+ multilingual labels for live concepts), hover a link for the relation, **click a node to centre the camera**, drag nodes to reshape the force layout, scroll to zoom, drag the background to rotate. Keyword chips toggle on/off.

Intended as the MONITOR-as-product explorer surface (Knitweb/monitor#1). It can later be wired to the gateway `GET /web` read contract (pinned in pulse #214) for a fully live, trustless graph.
