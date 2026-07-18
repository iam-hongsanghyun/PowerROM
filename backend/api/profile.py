from __future__ import annotations

import io

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.utils.excel_io import (
    generate_excel_from_json,
    load_profile_json,
    parse_excel_bytes,
    profile_to_workbook,
    save_uploaded_excel,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/profile/{code}
# ---------------------------------------------------------------------------

@router.get("/profile/{code}")
def get_profile(code: str) -> dict:
    """Return the full country profile as JSON (the authoritative JSON file)."""
    code = code.upper()
    try:
        return load_profile_json(code)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/profile/{code}/excel
# ---------------------------------------------------------------------------

@router.get("/profile/{code}/excel")
def download_excel(code: str) -> StreamingResponse:
    """Return the country profile as an .xlsx download.
    Re-generates from the current JSON so the file is always fresh.
    """
    code = code.upper()
    try:
        profile = load_profile_json(code)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    wb = profile_to_workbook(profile)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"PowerROM_{code}_profile.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /api/profile/{code}/excel  (upload)
# ---------------------------------------------------------------------------

@router.post("/profile/excel/parse")
async def parse_excel_profile(file: UploadFile) -> dict:
    """Parse an uploaded .xlsx and return the profile JSON — no disk writes.
    Vercel-safe: works on ephemeral / read-only filesystems.
    """
    if file.content_type not in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    ):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file.")
    raw = await file.read()
    try:
        profile = parse_excel_bytes(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse Excel: {exc}") from exc
    return profile


@router.post("/profile/{code}/excel")
async def upload_excel(code: str, file: UploadFile) -> dict:
    """Upload an edited .xlsx and overwrite the country profile on disk.
    Returns the parsed profile JSON so the frontend can update immediately.
    """
    code = code.upper()
    if file.content_type not in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    ):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file.")

    raw = await file.read()
    try:
        profile = save_uploaded_excel(code, raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse Excel: {exc}") from exc

    return profile


# ---------------------------------------------------------------------------
# PUT /api/profile/{code}  (save edits from GUI)
# ---------------------------------------------------------------------------

class ProfileUpdateRequest(BaseModel):
    profile: dict


@router.put("/profile/{code}")
def update_profile(code: str, body: ProfileUpdateRequest) -> dict:
    """Save a profile dict (from the GUI editor) back to the JSON file
    and regenerate the Excel file.
    """
    from backend.utils.excel_io import save_profile_json
    code = code.upper()
    try:
        save_profile_json(code, body.profile)
        generate_excel_from_json(code)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return body.profile
