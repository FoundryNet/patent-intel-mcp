"""Supabase PostgREST client for patent-intel-mcp (standalone patent-intel project).

Backs the patents cache, the patent_assignees summary, pgvector prior-art search
(match_patents RPC), the free-tier counter (patent_claim_free_query RPC), and the
x402 ledger (patent_payments). Defensive: failures degrade to None/[]/{}/False.
"""
from __future__ import annotations

import logging
from typing import Optional

import config
from http_util import request_json

logger = logging.getLogger("patent.supa")


def configured() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY)


def _headers(extra: Optional[dict] = None) -> dict:
    h = {"apikey": config.SUPABASE_SERVICE_KEY,
         "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
         "Content-Type": "application/json", "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


def _url(path: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{path}"


async def _select(table: str, params: dict, *, headers_extra: Optional[dict] = None) -> list:
    if not configured():
        return []
    r = await request_json("GET", _url(table), headers=_headers(headers_extra),
                           params=params, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return r
    logger.warning(f"supa select {table} failed: {r}")
    return []


async def _rpc(fn: str, body: dict):
    if not configured():
        return None
    return await request_json("POST", _url(f"rpc/{fn}"), headers=_headers(),
                              body=body, timeout=config.REQUEST_TIMEOUT)


_FIELDS = ("id,patent_number,application_number,title,abstract,filing_date,"
           "grant_date,publication_date,assignee_name,assignee_country,inventors,"
           "cpc_codes,cpc_primary,claims_count,citation_count,status,patent_type,"
           "source_url,created_at,updated_at")


# ── reads ─────────────────────────────────────────────────────────────────────
async def search_patents(*, keyword=None, assignee=None, cpc_code=None,
                         date_from=None, date_to=None, patent_type=None, limit=25) -> list:
    params = {"select": _FIELDS, "order": "grant_date.desc.nullslast",
              "limit": str(min(max(int(limit or 25), 1), 100))}
    if keyword:
        kw = keyword.replace("*", "").replace(",", " ")
        params["or"] = f"(title.ilike.*{kw}*,abstract.ilike.*{kw}*)"
    if assignee:
        params["assignee_name"] = f"ilike.*{assignee}*"
    if cpc_code:
        params["cpc_primary"] = f"ilike.{cpc_code}*"
    if patent_type:
        params["patent_type"] = f"eq.{patent_type}"
    if date_from and date_to:
        params["and"] = f"(grant_date.gte.{date_from},grant_date.lte.{date_to})"
    elif date_from:
        params["grant_date"] = f"gte.{date_from}"
    elif date_to:
        params["grant_date"] = f"lte.{date_to}"
    return await _select("patents", params)


async def patent_by_number(patent_number: str) -> Optional[dict]:
    rows = await _select("patents", {"select": _FIELDS,
                                     "patent_number": f"eq.{patent_number}", "limit": "1"})
    return rows[0] if rows else None


async def company_patents(company_name: str, date_from: Optional[str], limit=200) -> list:
    params = {"select": _FIELDS, "assignee_name": f"ilike.*{company_name}*",
              "order": "grant_date.desc.nullslast", "limit": str(limit)}
    if date_from:
        params["filing_date"] = f"gte.{date_from}"
    return await _select("patents", params)


async def assignee_summary(company_name: str) -> Optional[dict]:
    rows = await _select("patent_assignees",
                         {"select": "*", "assignee_name": f"ilike.*{company_name}*",
                          "order": "patent_count.desc", "limit": "1"})
    return rows[0] if rows else None


async def recent_for_trending(date_from: str, cpc=None, max_rows=10000) -> list:
    base = {"select": "cpc_primary,cpc_codes,assignee_name,grant_date,filing_date",
            "order": "grant_date.desc.nullslast"}
    base["or"] = f"(grant_date.gte.{date_from},filing_date.gte.{date_from})"
    if cpc:
        base["cpc_primary"] = f"ilike.{cpc}*"
    if not configured():
        return []
    out: list = []
    page = 1000
    for start in range(0, max_rows, page):
        r = await request_json("GET", _url("patents"),
                               headers=_headers({"Range-Unit": "items",
                                                 "Range": f"{start}-{start + page - 1}"}),
                               params=base, timeout=config.REQUEST_TIMEOUT)
        if not isinstance(r, list):
            break
        out.extend(r)
        if len(r) < page:
            break
    return out


async def digest_patents(*, cpc=None, assignee=None, date_from=None, limit=100) -> list:
    params = {"select": _FIELDS, "order": "grant_date.desc.nullslast", "limit": str(limit)}
    if date_from:
        params["or"] = f"(grant_date.gte.{date_from},publication_date.gte.{date_from})"
    if cpc:
        params["cpc_primary"] = f"ilike.{cpc}*"
    if assignee:
        params["assignee_name"] = f"ilike.*{assignee}*"
    return await _select("patents", params)


async def match_patents(embedding: list, match_count: int = 10) -> list:
    """pgvector prior-art search. Embedding passed as a '[...]' text literal."""
    vec = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
    r = await _rpc("match_patents", {"query_embedding": vec, "match_count": match_count})
    return r if isinstance(r, list) else []


# ── writes (aggregator) ───────────────────────────────────────────────────────
async def upsert_patents(rows: list) -> dict:
    if not configured() or not rows:
        return {"error": "not_configured"} if not configured() else {"data": []}
    r = await request_json("POST", _url("patents"),
                           headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                           params={"on_conflict": "patent_number"},
                           body=rows, timeout=max(config.REQUEST_TIMEOUT, 60))
    if isinstance(r, list):
        return {"data": r}
    return r if isinstance(r, dict) else {"error": "bad_response", "detail": str(r)}


async def refresh_assignee_summary() -> None:
    """Recompute patent_assignees from patents (server-side aggregation)."""
    await _rpc("refresh_patent_assignees", {})


async def upsert_assignees(rows: list) -> dict:
    if not configured() or not rows:
        return {"data": []}
    r = await request_json("POST", _url("patent_assignees"),
                           headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                           params={"on_conflict": "assignee_name"},
                           body=rows, timeout=max(config.REQUEST_TIMEOUT, 60))
    if isinstance(r, list):
        return {"data": r}
    return r if isinstance(r, dict) else {"error": "bad_response", "detail": str(r)}


# ── free-tier + payments ──────────────────────────────────────────────────────
async def claim_free_query(agent_key: str, day: str, cap: int) -> Optional[dict]:
    r = await _rpc("patent_claim_free_query",
                   {"p_agent_key": agent_key, "p_day": day, "p_cap": cap})
    if isinstance(r, dict) and "allowed" in r:
        return r
    if isinstance(r, list) and r and isinstance(r[0], dict):
        return r[0]
    logger.warning(f"claim_free_query rpc unexpected: {r}")
    return None


async def payment_tx_used(tx_signature: str) -> bool:
    rows = await _select("patent_payments",
                         {"tx_signature": f"eq.{tx_signature}", "select": "tx_signature", "limit": "1"})
    return bool(rows)


async def insert_payment(row: dict) -> dict:
    if not configured():
        return {"error": "not_configured"}
    r = await request_json("POST", _url("patent_payments"),
                           headers=_headers({"Prefer": "return=minimal"}),
                           body=row, timeout=config.REQUEST_TIMEOUT)
    if isinstance(r, list):
        return {"data": r}
    if isinstance(r, dict) and "error" not in r:
        return {"data": [r]}
    return r if isinstance(r, dict) else {"error": "bad_response", "detail": str(r)}
