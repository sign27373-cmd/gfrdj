"""
skiptrace.py -- the ONLY file that talks to Ava Data (https://avadata.ai/docs/).

Base URL (from Ava's docs): https://app.avadata.ai/api/v1   Auth: Bearer token.
Only AVA_API_KEY is needed (Render -> Environment). AVA_BASE_URL is no longer used.

Endpoints used:
  POST /standard-search  body: firstName, lastName(required), address, city, state, zip,
                               dataTypes ["phone"] | ["email"] | ["phone","email"]
  POST /deep-search      same, no dataTypes; also returns relatedPeople
  GET  /credits          free
Billing is per match only. Rate limit: 60 requests/minute per key.
"""

import json
import os
import random
import re
import time
import urllib.error
import urllib.request

BASE_URL = "https://app.avadata.ai/api/v1"
API_KEY = (os.environ.get("AVA_API_KEY") or "").strip()

MODES = [
    ("phone", "Standard: phone only", "1 credit (2 cents) if a match is found"),
    ("phone_email", "Standard: phone + email", "2 credits (4 cents) if a match is found"),
    ("deep", "Deep: adds relatives and associates", "10 credits (20 cents) if a match is found"),
]
_MODE = {"phone": ("standard-search", ["phone"]),
         "phone_email": ("standard-search", ["phone", "email"]),
         "deep": ("deep-search", None)}


class NotConfigured(Exception):
    pass


class AvaError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _http(method, path, body=None):
    if not API_KEY:
        raise NotConfigured("Skip trace is not connected: add AVA_API_KEY in Render -> Environment.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path, data=data, method=method,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json",
                 "Accept": "application/json"})
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                msg = json.loads(text).get("error") or text
            except ValueError:
                msg = text
            if e.code == 429 and attempt < 3:
                time.sleep(2 ** attempt + random.random())
                continue
            hints = {401: " (key invalid, or API access not enabled: email support@avadata.ai)",
                     402: " (not enough credits)", 429: " (over 60 requests/minute)"}
            raise AvaError(e.code, f"Ava error {e.code}: {msg}{hints.get(e.code, '')}")
        except urllib.error.URLError as e:
            if attempt == 3:
                raise AvaError(0, f"Could not reach Ava: {e}")
            time.sleep(2 ** attempt)


def get_credits():
    try:
        return _http("GET", "/credits").get("data")
    except Exception:
        return None


def default_address_choice(mailing):
    street = (mailing.get("street") or "").strip().upper()
    if street and not re.match(r"^(P\.?\s*O\.?\s*BOX|POB|PO BOX)", street):
        return "mailing"
    return "property"


def pick_address(prop, mailing, choice):
    if choice == "mailing" and (mailing.get("street") or "").strip():
        return mailing["street"], mailing.get("city"), mailing.get("state"), mailing.get("zip")
    return prop.get("address_line"), prop.get("city"), prop.get("state"), prop.get("zip")


def build_request(mode, first, last, address, city, state, zip_):
    endpoint, data_types = _MODE[mode]
    body = {"firstName": (first or "").strip(), "lastName": (last or "").strip()}
    for key, val in (("address", address), ("city", city), ("state", state), ("zip", zip_)):
        if val and str(val).strip():
            body[key] = str(val).strip()
    if data_types:
        body["dataTypes"] = data_types
    return {"method": "POST", "url": f"{BASE_URL}/{endpoint}", "path": f"/{endpoint}", "body": body}


def _key(street, zip_):
    toks = re.sub(r"[^A-Z0-9 ]", "", (street or "").upper()).split()
    return (toks[0] if toks else "", toks[1] if len(toks) > 1 else "", (zip_ or "")[:5])


def normalize(mode, resp):
    d = (resp or {}).get("data") or {}
    subj = (d.get("subject") or {}) if mode == "deep" else d
    full = lambda x: " ".join(filter(None, [x.get("firstName"), x.get("lastName")]))
    phones = lambda x: [{"number": p.get("number"), "type": p.get("type")} for p in x.get("phones") or []]
    return {
        "matched": bool(d.get("matchFound")), "credits": d.get("creditsCharged"),
        "phones": phones(subj),
        "emails": [e.get("address") if isinstance(e, dict) else e for e in subj.get("emails") or []],
        "name": subj.get("name") or full(subj), "addresses": subj.get("addresses") or [],
        "match_type": d.get("matchType"), "deceased": subj.get("deceased"), "age": subj.get("age"),
        "related": [{"name": full(r), "relationship": r.get("relationship"),
                     "deceased": r.get("deceased"), "phones": phones(r)}
                    for r in d.get("relatedPeople") or []],
    }


def assess(first, last, norm, known_addresses):
    """Checks to help confirm Ava returned the right person."""
    checks = []
    nm = (norm["name"] or "").upper()
    if nm:
        ok = first.upper() in nm and last.upper() in nm
        checks.append({"ok": ok, "label": "Name matches" if ok else f"Name differs: {norm['name']}"})
    if norm["addresses"]:
        keys = {_key(s, z) for s, z in known_addresses if s}
        hit = any(_key(a.get("street"), a.get("zip")) in keys for a in norm["addresses"])
        checks.append({"ok": hit, "label": "Address history includes the property or mailing address"
                       if hit else "Address history does NOT include the property or mailing address"})
    if norm["deceased"]:
        checks.append({"ok": False, "label": "Reported as deceased: possible probate lead"})
    return checks


def run_trace(mode, req, first, last, known_addresses):
    resp = _http("POST", req["path"], req["body"])
    norm = normalize(mode, resp)
    return {"norm": norm, "checks": assess(first, last, norm, known_addresses), "response": resp}
