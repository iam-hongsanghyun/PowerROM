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
| `calculate_lcoe` | Price a fleet: hourly dispatch → system LCOE, emission intensity, curtailment, unserved energy, import dependency, per-tech LCOE, realised mix. Exposes **every policy lever**: carbon price, EV load, min/max CF limits, manual merit order, storage (power + duration), capacity expansion, an RPS target + penalty, ITC/PTC clean-energy subsidies, a fuel-import tariff, demand-shape controls, ensemble/weather-year settings, and profile overrides. Optional `include_dispatch` adds the hourly-dispatch digest. |
| `run_dispatch` | Run the 8760-hour dispatch and return a compact digest: per-generator capacity factor / energy / share, scalar metrics (curtailment, unserved), and a Load-Duration-Curve summary. |
| `lcoe_vs_vre_curve` | Sweep the renewable share 0→max and return the LCOE / emissions / curtailment frontier — traces the cost of decarbonisation. |
| `simulate_decarbonisation_pathway` | Run a plan from today's fleet to a target-year mix (phase-outs, escalating carbon price, demand growth), optionally growing selected resources to meet load each year. |
| `size_firm_capacity_for_reliability` | Minimum GW of one firm resource to meet a reliability standard (LOLE ≤ target h/yr). |
| `size_least_cost_mix_for_reliability` | Co-size the least-cost combination of selected resources to meet the reliability standard. |
| `validate_generator_config` | Validate a generator config (which fields are fitted / defaulted / missing per component). |
| `fit_curve` | Fit a parametric curve (linear, logarithmic, power, …) to (x, y) points; returns params, R², 95% CIs. |

Generators are `solar`, `wind_onshore`, `wind_offshore`, `gas_ccgt`, `coal`, `nuclear`, `other`;
storage is `storage` (short + long tiers). These ten tools cover the full modelling surface of the
web app.

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

## Remote (hosted on Vercel)

The server is also exposed over MCP's **Streamable-HTTP** transport at:

```
https://power-rom.vercel.app/mcp/
```

Any MCP client can connect by URL — no local install. With Claude Code:

```bash
claude mcp add --transport http powerrom https://power-rom.vercel.app/mcp/
```

It runs stateless inside the same Vercel Python function that serves the API, wired into the app
lifespan (`backend/main.py`), and routed via `vercel.json`. DNS-rebinding host protection is
disabled because it is a hosted public endpoint.

## Register with Claude Code (local stdio)

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
