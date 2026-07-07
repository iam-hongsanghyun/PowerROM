# PowerROM data + dev tasks. Uses the `powerrom` conda env by default; override with PY=...
PY ?= conda run --no-capture-output -n powerrom python

.PHONY: help data data-refresh hourly test mcp

help:  ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

data:  ## Rebuild country profiles from the cached Ember dataset
	$(PY) -m backend.data.build_country_profiles

data-refresh:  ## Download the latest Ember release, then rebuild country profiles
	$(PY) -m backend.data.build_country_profiles --download

hourly:  ## Rebuild real hourly weather-year profiles from PVGIS (network, ~4 min)
	$(PY) -m backend.data.build_hourly_profiles

test:  ## Run the backend test suite
	$(PY) -m pytest backend/tests -q

mcp:  ## Run the PowerROM MCP server (stdio)
	$(PY) -m backend.mcp_server
