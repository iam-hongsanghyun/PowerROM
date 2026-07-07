from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.calculate import router as calculate_router
from backend.api.countries import router as countries_router
from backend.api.dispatch import router as dispatch_router
from backend.api.fit import router as fit_router
from backend.api.profile import router as profile_router
from backend.api.validate import router as validate_router

app = FastAPI(title="PowerROM API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3100",  # PowerROM dev frontend (see run.command)
        "http://localhost:3000",  # legacy/default Next.js port, kept for flexibility
        "https://powerrom.vercel.app",
        "https://*.vercel.app",
    ],
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
