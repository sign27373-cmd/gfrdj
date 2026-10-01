"""
wh_dashboard - personal lead-management UI for the wholesaling pipeline.

Run locally:
    pip install -r requirements.txt
    cp .env.example .env   # fill in real values
    uvicorn main:app --reload

Deploy: see README.md for Render steps.
"""

import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
APP_PASSWORD = os.environ.get("APP_PASSWORD")  # required in production, see auth below

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Public Supabase Storage bucket holding the Street View photos.
# Files look like "0018_2825-landau-ct-hend-nv-89074_house.jpg"; we match a
# lead to its photo by street address + state + zip (the leading number and
# the city abbreviation are ignored).
PHOTO_BUCKET = "property-photos"
PHOTO_FOLDER = "unknown"
PHOTO_CACHE_SECONDS = 600
_photo_cache = {"at": 0.0, "items": []}


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _load_photo_index():
    now = time.time()
    if _photo_cache["items"] and now - _photo_cache["at"] < PHOTO_CACHE_SECONDS:
        return _photo_cache["items"]
    items, offset = [], 0
    try:
        while True:
            batch = supabase.storage.from_(PHOTO_BUCKET).list(
                PHOTO_FOLDER, {"limit": 1000, "offset": offset}
            )
            for f in batch:
                m = re.match(r"^\d+_(.+?)(?:_house)?\.jpe?g$", f["name"], re.I)
                if m:
                    items.append((m.group(1).lower(), f["name"]))
            if len(batch) < 1000:
                break
            offset += 1000
    except Exception as e:
        print(f"photo index load failed: {e}")
        return _photo_cache["items"]
    _photo_cache.update(at=now, items=items)
    return items


def photo_url_for(prop):
    """Public bucket URL of the photo matching this property's address, or None."""
    addr = _slugify(prop.get("address_line"))
    state = (prop.get("state") or "").lower()
    zip5 = (prop.get("zip") or "")[:5]
    if not addr or not state or not zip5:
        return None
    suffix = f"-{state}-{zip5}"
    for slug, name in _load_photo_index():
        if slug.startswith(addr + "-") and slug.endswith(suffix):
            return (f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/"
                    f"{PHOTO_BUCKET}/{PHOTO_FOLDER}/{quote(name)}")
    return None


app = FastAPI(title="wh_dashboard")
BASE_DIR = Path(__file__).resolve().parent
# Flat layout: templates and style.css live next to main.py (no subfolders).
templates = Jinja2Templates(directory=str(BASE_DIR))


@app.get("/style.css", include_in_schema=False)
def stylesheet():
    return FileResponse(BASE_DIR / "style.css", media_type="text/css")

security = HTTPBasic()

PAGE_SIZE = 50
SORT_COLUMNS = {
    "assessed_value": "Assessed value",
    "market_value_estimate": "Market value",
    "arv_estimate": "ARV estimate",
    "estimated_equity": "Estimated equity",
    "living_sqft": "Living sqft",
}

PIPELINE_JOBS = {
    # label: (module name, function name) -- each must expose main() with no
    # args and write its own progress to wh_pipeline_runs. These are only
    # the Supabase-only enrichment steps; raw-file ingestion (normalize/
    # combine/import) still needs to be run locally, see README.
    "nv_assessed_value": "Nevada: assessed value",
    "nv_sqft": "Nevada: living sqft",
    "nv_property_use": "Nevada: property use",
    "wa_assessed_value": "Washington: assessed value",
    "wa_sqft": "Washington: living sqft",
    "compute_arv": "Compute ARV (all states)",
}


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not APP_PASSWORD:
        # No password configured -- allow through, but this should only
        # ever happen in local dev. Render deploy requires APP_PASSWORD.
        return True
    correct = secrets.compare_digest(credentials.password, APP_PASSWORD)
    if not correct:
        raise HTTPException(
            status_code=401,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


def fetch_all_paginated(table, select, filters=None, page_size=1000):
    """Supabase/PostgREST caps a single query at 1000 rows -- this pages
    through until everything is retrieved. Use for counts/aggregates only;
    the leads browser itself uses server-side range() for real pagination."""
    rows = []
    start = 0
    query_builder = supabase.table(table).select(select)
    if filters:
        query_builder = filters(query_builder)
    while True:
        batch = query_builder.range(start, start + page_size - 1).execute().data
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


@app.get("/")
def dashboard(request: Request, _: bool = Depends(require_auth)):
    rows = fetch_all_paginated(
        "wh_properties",
        "state, assessed_value, market_value_estimate, arv_estimate, "
        "estimated_equity, living_sqft",
    )

    by_state = {}
    for r in rows:
        s = r.get("state") or "?"
        d = by_state.setdefault(s, {
            "total": 0, "with_value": 0, "with_arv": 0,
            "with_real_equity": 0, "with_sqft": 0,
        })
        d["total"] += 1
        if r.get("assessed_value") or r.get("market_value_estimate"):
            d["with_value"] += 1
        if r.get("arv_estimate"):
            d["with_arv"] += 1
        if r.get("estimated_equity") is not None:
            d["with_real_equity"] += 1
        if r.get("living_sqft"):
            d["with_sqft"] += 1

    vacancy_rows = fetch_all_paginated("wh_vacancy_checks", "property_id")
    deal_rows = fetch_all_paginated("wh_deals", "stage")
    stage_counts = {}
    for d in deal_rows:
        stage_counts[d["stage"]] = stage_counts.get(d["stage"], 0) + 1

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "by_state": by_state,
        "total_properties": len(rows),
        "total_vacancy_checks": len(vacancy_rows),
        "stage_counts": stage_counts,
    })


@app.get("/leads")
def leads(
    request: Request,
    state: str = "",
    property_use: str = "",
    city: str = "",
    search: str = "",
    sort: str = "assessed_value",
    direction: str = "desc",
    page: int = 1,
    _: bool = Depends(require_auth),
):
    if sort not in SORT_COLUMNS:
        sort = "assessed_value"
    page = max(1, page)

    query = supabase.table("wh_properties").select(
        "id, state, county, address_line, city, zip, property_use, "
        "assessed_value, market_value_estimate, arv_estimate, "
        "estimated_equity, living_sqft, parcel_id",
        count="exact",
    )
    if state:
        query = query.eq("state", state)
    if property_use:
        query = query.ilike("property_use", f"%{property_use}%")
    if city:
        query = query.ilike("city", f"%{city}%")
    if search:
        query = query.or_(
            f"address_line.ilike.%{search}%,parcel_id.ilike.%{search}%"
        )

    query = query.order(sort, desc=(direction == "desc"))
    start = (page - 1) * PAGE_SIZE
    result = query.range(start, start + PAGE_SIZE - 1).execute()

    total = result.count or 0
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse("leads.html", {
        "request": request,
        "leads": result.data,
        "state": state,
        "property_use": property_use,
        "city": city,
        "search": search,
        "sort": sort,
        "direction": direction,
        "sort_columns": SORT_COLUMNS,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@app.get("/leads/{property_id}")
def lead_detail(request: Request, property_id: str, _: bool = Depends(require_auth)):
    prop = (
        supabase.table("wh_properties").select("*").eq("id", property_id)
        .single().execute().data
    )
    if not prop:
        raise HTTPException(status_code=404, detail="Lead not found")

    owners = (
        supabase.table("wh_owners").select("*").eq("property_id", property_id)
        .execute().data
    )
    vacancy_checks = (
        supabase.table("wh_vacancy_checks").select("*").eq("property_id", property_id)
        .order("checked_at", desc=True).execute().data
    )
    deal = (
        supabase.table("wh_deals").select("*").eq("property_id", property_id)
        .order("created_at", desc=True).limit(1).execute().data
    )
    deal = deal[0] if deal else None
    contact_log = (
        supabase.table("wh_contact_log").select("*").eq("property_id", property_id)
        .order("contacted_at", desc=True).execute().data
    )

    return templates.TemplateResponse("lead_detail.html", {
        "request": request,
        "prop": prop,
        "owners": owners,
        "vacancy_checks": vacancy_checks,
        "photo_url": photo_url_for(prop),
        "deal": deal,
        "contact_log": contact_log,
        "deal_stages": ["lead", "contacted", "negotiating", "under_contract",
                         "assigned", "closed", "dead"],
    })


@app.post("/leads/{property_id}/stage")
def update_stage(property_id: str, stage: str = Form(...), _: bool = Depends(require_auth)):
    existing = (
        supabase.table("wh_deals").select("id").eq("property_id", property_id)
        .order("created_at", desc=True).limit(1).execute().data
    )
    if existing:
        supabase.table("wh_deals").update({"stage": stage}).eq(
            "id", existing[0]["id"]
        ).execute()
    else:
        supabase.table("wh_deals").insert(
            {"property_id": property_id, "stage": stage}
        ).execute()
    return RedirectResponse(url=f"/leads/{property_id}", status_code=303)


@app.post("/leads/{property_id}/log")
def add_contact_log(
    property_id: str,
    channel: str = Form(...),
    outcome: str = Form(""),
    notes: str = Form(""),
    _: bool = Depends(require_auth),
):
    supabase.table("wh_contact_log").insert({
        "property_id": property_id,
        "channel": channel,
        "outcome": outcome or None,
        "notes": notes or None,
    }).execute()
    return RedirectResponse(url=f"/leads/{property_id}", status_code=303)


@app.get("/pipeline")
def pipeline(request: Request, _: bool = Depends(require_auth)):
    runs = (
        supabase.table("wh_pipeline_runs").select("*")
        .order("started_at", desc=True).limit(30).execute().data
    )
    return templates.TemplateResponse("pipeline.html", {
        "request": request,
        "runs": runs,
        "jobs": PIPELINE_JOBS,
    })


def _run_job_placeholder(job_key: str, run_id: str):
    """Placeholder background runner. Wire each job_key to its real script's
    main() here once those scripts are refactored to report progress into
    wh_pipeline_runs instead of only printing to a terminal. See README."""
    supabase.table("wh_pipeline_runs").update({
        "status": "failed",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "params": {"note": f"'{job_key}' not wired up yet -- see README Phase 2"},
    }).eq("id", run_id).execute()


@app.post("/pipeline/run/{job_key}")
def trigger_job(job_key: str, background_tasks: BackgroundTasks, _: bool = Depends(require_auth)):
    if job_key not in PIPELINE_JOBS:
        raise HTTPException(status_code=404, detail="Unknown job")
    run = supabase.table("wh_pipeline_runs").insert({
        "job_type": "other",
        "status": "running",
        "params": {"job_key": job_key, "label": PIPELINE_JOBS[job_key]},
    }).execute().data[0]
    background_tasks.add_task(_run_job_placeholder, job_key, run["id"])
    return RedirectResponse(url="/pipeline", status_code=303)
