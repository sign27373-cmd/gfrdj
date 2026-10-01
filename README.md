# wh dashboard

Personal lead-management UI for the wholesaling pipeline (NV/WA/TN leads
in Supabase). Phase 1: dashboard + leads browser + lead detail (with
deal-stage and call-log CRM actions). Pipeline job triggering is scaffolded
but not yet wired to real enrichment logic -- see Phase 2 below.

## Run locally

```
pip install -r requirements.txt
cp .env.example .env
# edit .env with your real SUPABASE_URL, SUPABASE_KEY, and a real APP_PASSWORD
uvicorn main:app --reload
```

Open http://localhost:8000 -- your browser will prompt for a username
(leave blank) and the password you set in `.env`.

## Deploy to Render

1. Push this folder to a GitHub repo (private repo recommended, since
   `render.yaml` and the code itself never contain real secrets -- only
   `.env`, which is gitignored, does).
2. In Render: New -> Blueprint -> connect the repo. Render reads
   `render.yaml` automatically.
3. When prompted, set the three environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_KEY` (the service-role key)
   - `APP_PASSWORD` (pick a real password -- without this set, the app
     has NO login protection and anyone with the URL can see owner names,
     mailing addresses, and (once skip tracing starts) phone/email data)
4. Deploy. Render gives you a `https://wh-dashboard-xxxx.onrender.com` URL.

On the free plan, Render spins the service down after inactivity and takes
~30-60 seconds to wake back up on the next visit. Fine for personal use;
upgrade the plan if that delay becomes annoying.

## What's built

- **Dashboard** (`/`): per-state counts of total leads, how many have a
  value/ARV/real-equity/sqft figure, and a breakdown of deal stages.
- **Leads browser** (`/leads`): filter by state, property use, city, or a
  text search on address/parcel ID; sort by any numeric field; paginated
  (50 per page, matching Supabase's query cap pattern used throughout
  the rest of this project).
- **Lead detail** (`/leads/{id}`): full property record, owner(s),
  the most recent vacancy check (if any), a deal-stage selector, and a
  call/text/email log with an add-entry form.

## What's NOT built yet (known gaps, on purpose)

1. **Pipeline job execution is a placeholder.** The `/pipeline` page
   creates a real row in `wh_pipeline_runs` and marks it `failed` with a
   note, rather than silently pretending to succeed. To make a job real:
   take the existing script's logic (e.g. `enrich_nv_sqft.py`), refactor
   its loop to periodically `update` the `wh_pipeline_runs` row
   (`processed_items`, `total_items`, `status`), and call that function
   from `_run_job_placeholder` in `main.py` instead of the stub.
   Do this one job at a time rather than all six at once, since each
   script has its own retry/backoff behavior worth preserving carefully.

2. **Raw-file ingestion can't run from the hosted app at all.** Render's
   filesystem doesn't persist your local `wh` folder, so
   `normalize_leads.py`, `combine_leads.py`, `import_leads.py`, and
   `import_propwire_tn.py` (which all read a file from disk) have to stay
   command-line steps run from your own machine. If you want this done
   from the UI too, that needs a file-upload endpoint added here -- ask
   for it as its own piece of work rather than assuming it's included.

3. **Vacancy-check photos aren't viewable yet.** `streetview_fetch.py`
   saves `.jpg` files to your local disk; `wh_vacancy_checks.image_url`
   is empty for those rows (only `image_local_path` is set), so the
   detail page says so honestly instead of showing a broken image. To
   fix: upload each photo somewhere web-reachable (e.g. Supabase
   Storage) during the Street View run, and store that public URL in
   `image_url` instead of (or alongside) the local path.

4. **No skip-trace UI yet.** `wh_skip_traces` and `wh_contacts` exist in
   the schema but aren't surfaced here. Natural next addition once skip
   tracing itself is built.
