"""
skiptrace.py -- the ONLY file that talks to the skip trace provider (Ava Data).

run_trace() must return:
  {"phones": [{"number": "...", "type": "mobile"}], "emails": ["..."], "raw": {...}}

Set these in Render -> Environment (never in the code):
  AVA_API_KEY   your Ava API key (Bearer token)
  AVA_BASE_URL  the API base URL from Ava's API docs
"""

import os

AVA_API_KEY = os.environ.get("AVA_API_KEY")
AVA_BASE_URL = os.environ.get("AVA_BASE_URL")


class NotConfigured(Exception):
    pass


def run_trace(prop, owner_name, deep=False):
    if not AVA_API_KEY or not AVA_BASE_URL:
        raise NotConfigured("Skip trace is not connected yet: add AVA_API_KEY and AVA_BASE_URL in Render.")
    # TODO: call Ava's skip trace endpoint here using Authorization: Bearer <AVA_API_KEY>,
    # sending prop["address_line"], prop["city"], prop["state"], prop["zip"] and owner_name,
    # then map the response into the format described at the top of this file.
    raise NotConfigured("Ava endpoint details still need to be added to skiptrace.py.")
