# PowerROM MCP server

An [MCP](https://modelcontextprotocol.io) server that exposes the PowerROM reduced-order
electricity-system model as tools an AI agent can call directly. It wraps the same engine the web
app uses (`backend/core/lcoe_engine.py`) in-process — no HTTP server required — and returns compact
JSON summaries (scalars + small maps), not raw 8760-hour arrays, so results fit an agent's context.

## Tools

| Tool | What it does |
|------|--------------|
| `list_countries` | Every modelled country with its real Ember data (demand, installed capacity by tech, generation mix, data year). |
| `get_country_profile` | Full technology profile for a country (capex/opex, fuel price, heat rate, emission factor, capacity factor, storage, discount rate, sources). |
| `calculate_lcoe` | Price a fleet: hourly dispatch → system LCOE, emission intensity, curtailment, unserved energy, import dependency, per-tech LCOE, realised mix. Supports carbon price, EV load, min/max CF limits, storage, and capacity expansion to meet 100% load. |
| `simulate_decarbonisation_pathway` | Run a plan from today's fleet to a target-year mix (phase-outs, escalating carbon price, demand growth), optionally growing selected resources to meet load each year. |
| `size_firm_capacity_for_reliability` | Minimum GW of one firm resource to meet a reliability standard (LOLE ≤ target h/yr). |
| `size_least_cost_mix_for_reliability` | Co-size the least-cost combination of selected resources to meet the reliability standard. |

Generators are `solar`, `wind_onshore`, `gas_ccgt`, `coal`, `nuclear`, `other`; storage is `storage`
(short + long tiers).

## Install & run

The server needs the `mcp` SDK on top of the backend dependencies:

```bash
conda run -n powerrom pip install -r requirements-mcp.txt
# or, in an active env:
pip install -r requirements-mcp.txt
```

`mcp` is kept in a separate `requirements-mcp.txt` (not `requirements.txt`) so the Vercel
serverless function stays lean.

Run it directly over stdio:

```bash
python -m backend.mcp_server
```

## Register with Claude Code

The repo ships a project-scoped [`.mcp.json`](../.mcp.json):

```json
{
  "mcpServers": {
    "powerrom": {
      "command": "conda",
      "args": ["run", "--no-capture-output", "-n", "powerrom", "python", "-u", "-m", "backend.mcp_server"]
    }
  }
}
```

Restart Claude Code in this directory (and approve the server) to load it. If `conda` isn't on your
`PATH`, replace `command`/`args` with the absolute interpreter path, e.g.
`"command": "/path/to/envs/powerrom/bin/python", "args": ["-u", "-m", "backend.mcp_server"]`.

## Example

> "Using powerrom, price Germany's current fleet at a $120/t carbon price, then find the least-cost
> way to add solar, wind and storage to fully meet load."

The agent calls `calculate_lcoe(country="DE", carbon_price=120)` then
`size_least_cost_mix_for_reliability(country="DE", capacities_gw=…, expandable=["solar","wind_onshore","storage"])`.
