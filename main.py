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
from urllib.parse import quote, urlencode
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


LEAD_SORTS = {
    "assessed_value": "Assessed", "market_value_estimate": "Market", "arv_estimate": "ARV",
    "estimated_equity": "Equity", "equity_pct": "Equity %", "bid_pct_arv": "Bid % of ARV",
    "days_to_auction": "Days to auction", "auction_date": "Auction date",
    "notice_date": "Notice date", "amount_in_default": "In default",
    "opening_bid": "Opening bid", "living_sqft": "Sqft", "city": "City",
}
USE_GROUPS = ["Single family", "Townhouse", "Manufactured", "Multi-family",
              "Commercial/Other", "Vacant land", "Unknown", "Other"]
# (url name, view column) -> params min_<name> / max_<name>
RANGES = [("assessed", "assessed_value"), ("market", "market_value_estimate"),
          ("arv", "arv_estimate"), ("equity", "estimated_equity"), ("eqpct", "equity_pct"),
          ("bidpct", "bid_pct_arv"), ("bid", "opening_bid"), ("default", "amount_in_default"),
          ("sqft", "living_sqft")]
PRESETS = [
    ("All", ""),
    ("Auctions in 30 days", "lead_type=pre_foreclosure&auction_within=30&sort=days_to_auction&direction=asc"),
    ("High equity NV", "state=NV&min_equity=100000&sort=estimated_equity&direction=desc"),
    ("Newest notices", "lead_type=pre_foreclosure&sort=notice_date&direction=desc"),
]
PER_PAGE_CHOICES = (50, 100, 200)
NON_FILTER_KEYS = {"sort", "direction", "page", "per"}


def _num(v):
    try:
        if v in (None, ""):
            return None
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


@app.get("/leads")
def leads(request: Request, _: bool = Depends(require_auth)):
    qp = request.query_params
    sort = qp.get("sort") if qp.get("sort") in LEAD_SORTS else "assessed_value"
    direction = "asc" if qp.get("direction") == "asc" else "desc"
    per = int(qp["per"]) if qp.get("per", "").isdigit() and int(qp["per"]) in PER_PAGE_CHOICES else 50
    page = max(1, int(qp["page"])) if qp.get("page", "").isdigit() else 1

    query = supabase.table("wh_leads_v").select(
        "id, state, county, address_line, address_note, city, zip, use_group, lead_type, "
        "auction_date, days_to_auction, assessed_value, market_value_estimate, arv_estimate, "
        "arv_method, estimated_equity, equity_pct, opening_bid, bid_pct_arv, "
        "amount_in_default, living_sqft, owner_occupied",
        count="exact",
    )
    states = [x for x in qp.getlist("state") if x]
    groups = [x for x in qp.getlist("use_group") if x]
    if states:
        query = query.in_("state", states)
    if groups:
        query = query.in_("use_group", groups)
    if qp.get("lead_type"):
        query = query.eq("lead_type", qp["lead_type"])
    for key in ("county", "city", "subdivision", "zip"):
        if qp.get(key):
            query = query.ilike(key, f"%{qp[key]}%")
    if qp.get("q"):
        q = qp["q"].replace(",", " ")
        query = query.or_(f"address_line.ilike.%{q}%,parcel_id.ilike.%{q}%")
    for name, col in RANGES:
        lo, hi = _num(qp.get(f"min_{name}")), _num(qp.get(f"max_{name}"))
        if lo is not None:
            query = query.gte(col, lo)
        if hi is not None:
            query = query.lte(col, hi)
    within = _num(qp.get("auction_within"))
    if within is not None:
        query = query.gte("days_to_auction", 0).lte("days_to_auction", within)
    if qp.get("has_arv") == "1":
        query = query.not_.is_("arv_estimate", "null")
    if qp.get("owner_occupied") in ("true", "false"):
        query = query.eq("owner_occupied", qp["owner_occupied"] == "true")
    if qp.get("show_incomplete") != "1":
        query = query.eq("has_address", True)

    query = query.order(sort, desc=(direction == "desc"), nullsfirst=False)
    start = (page - 1) * per
    result = query.range(start, start + per - 1).execute()
    total = result.count or 0
    for row in result.data:
        row["photo"] = photo_url_for(row)

    soon = (supabase.table("wh_leads_v").select("id", count="exact")
            .gte("days_to_auction", 0).lte("days_to_auction", 14).limit(1).execute().count or 0)

    params = list(qp.multi_items())
    chips = []
    for i, (k, v) in enumerate(params):
        if k in NON_FILTER_KEYS or v == "":
            continue
        rest = params[:i] + params[i + 1:]
        chips.append((f"{k.replace('_', ' ')}: {v}", "/leads?" + urlencode(rest)))

    return templates.TemplateResponse("leads.html", {
        "request": request, "leads": result.data, "qp": qp, "states": states, "groups": groups,
        "sort": sort, "direction": direction, "per": per, "page": page,
        "total": total, "total_pages": max(1, (total + per - 1) // per), "soon": soon,
        "sorts": LEAD_SORTS, "use_groups": USE_GROUPS, "presets": PRESETS,
        "per_choices": PER_PAGE_CHOICES, "chips": chips,
        "page_qs": urlencode([p for p in params if p[0] != "page"]),
        "sort_qs": urlencode([p for p in params if p[0] not in ("sort", "direction", "page")]),
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
