from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.calculate import router as calculate_router
from backend.api.countries import router as countries_router
from backend.api.dispatch import router as dispatch_router
from backend.api.fit import router as fit_router
from backend.api.pathway import router as pathway_router
from backend.api.profile import router as profile_router
from backend.api.validate import router as validate_router

# Optional remote MCP endpoint: exposes the PowerROM tools over MCP's Streamable-HTTP transport at
# /mcp. Guarded so the API still runs if the `mcp` SDK isn't installed (it lives in
# requirements-mcp.txt for local stdio use; add it to requirements.txt to enable /mcp on Vercel).
try:
    from backend.mcp_server import mcp as _mcp

    _mcp_app = _mcp.streamable_http_app()
except Exception:  # noqa: BLE001 — MCP is optional
    _mcp = None
    _mcp_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _mcp_app is not None:
        async with _mcp.session_manager.run():
            yield
    else:
        yield


app = FastAPI(title="PowerROM API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3611",  # PowerROM dev frontend (see run.command)
        "http://localhost:3100",  # previous PowerROM port, kept during transition
        "http://localhost:3000",  # legacy/default Next.js port, kept for flexibility
        "https://powerrom.vercel.app",
        "https://*.vercel.app",
    ],
    # Any localhost / 127.0.0.1 port — Next dev auto-assigns a new port when one is taken. Dev only.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calculate_router, prefix="/api", tags=["calculate"])
app.include_router(dispatch_router, prefix="/api", tags=["dispatch"])
app.include_router(fit_router, prefix="/api", tags=["fit"])
app.include_router(validate_router, prefix="/api", tags=["validate"])
app.include_router(countries_router, prefix="/api", tags=["countries"])
app.include_router(profile_router, prefix="/api", tags=["profile"])
app.include_router(pathway_router, prefix="/api", tags=["pathway"])


if _mcp_app is not None:
    app.mount("/mcp", _mcp_app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mcp": "enabled" if _mcp_app is not None else "disabled"}
