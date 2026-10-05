"""names.py -- turn assessor-style owner names ("BROWN CALVIN & BRENDA") into people."""

import re

ENTITY_RE = re.compile(
    r"\b(LLC|L\.L\.C|INC|CORP|CORPORATION|CO|COMPANY|LP|LLP|LTD|TRUST|TR|BANK|HOLDINGS|PROPERTIES|"
    r"INVESTMENTS?|ASSOC|ASSOCIATION|CHURCH|CITY OF|COUNTY OF|STATE OF|ESTATE OF|HEIRS OF|"
    r"HOMES|REALTY|VENTURES|PARTNERS|GROUP|FUND|MORTGAGE|HOUSING|AUTHORITY)\b")
NOISE_RE = re.compile(r"\b(ET\s*AL|ET\s*UX|ET\s*VIR|ETAL|ETUX|ETVIR|DECEASED|DECD|LIFE ESTATE|LE)\b")
SUFFIXES = {"JR", "SR", "II", "III", "IV"}


def _clean_tokens(part):
    toks = [t for t in re.split(r"[\s,]+", part.strip()) if t]
    return [t.strip(".") for t in toks if t.strip(".") and t.strip(".") not in SUFFIXES]


def _title(s):
    return " ".join(w.capitalize() for w in s.split())


def parse_people(full_name, last_first=True):
    """Return [{"first","middle","last"}] or [{"entity": True, "raw": ...}]."""
    s = (full_name or "").upper().strip()
    if not s:
        return []
    if ENTITY_RE.search(s):
        return [{"entity": True, "raw": full_name}]
    s = NOISE_RE.sub(" ", re.sub(r"\s+AND\s+", " & ", s))
    parts = [p for p in re.split(r"[&/;]", s) if p.strip()]
    people, shared_last = [], ""
    for i, part in enumerate(parts):
        if "," in part:
            last, _, rest = part.partition(",")
            last, toks = last.strip(), _clean_tokens(rest)
            first, middle = (toks[0] if toks else ""), " ".join(toks[1:])
        else:
            toks = _clean_tokens(part)
            if not toks:
                continue
            own_last = i == 0 or (len(toks) > 1 and len(toks[-1]) > 1)
            if i > 0 and not own_last:
                last, first, middle = shared_last, toks[0], " ".join(toks[1:])
            elif last_first:
                last, first, middle = toks[0], (toks[1] if len(toks) > 1 else ""), " ".join(toks[2:])
            else:
                first, last, middle = toks[0], (toks[-1] if len(toks) > 1 else ""), " ".join(toks[1:-1])
        shared_last = shared_last or last
        people.append({"first": _title(first), "middle": _title(middle), "last": _title(last)})
    return people
