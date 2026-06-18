"""USPTO Open Data Portal (ODP) patent source.

PatentsView was fully retired in 2025 and redirects to data.uspto.gov; its
search.patentsview.org host no longer resolves. The live replacement is the USPTO
Open Data Portal Patent File Wrapper Search API at api.uspto.gov, which needs a
free X-API-KEY (data.uspto.gov).

Reality of the source: the File Wrapper search metadata has title, dates,
assignee (applicant), inventors, CPC, and patent type — but NOT the abstract or
citation count (those live in the per-patent grant XML). So abstract and
citation_count are null here, and embeddings are built from the title. Fetching
grant XML to backfill abstracts is a documented future enrichment.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import config
from http_util import request_json

logger = logging.getLogger("patent.source")

_ENDPOINT = "https://api.uspto.gov/api/v1/patent/applications/search"


def configured() -> bool:
    return bool(config.PATENTSVIEW_API_KEY)


def _gp_url(num: str) -> str:
    return f"https://patents.google.com/patent/US{num}"


def _norm_cpc(code: str) -> str:
    # "A01G  25/162" -> "A01G25/162"
    return re.sub(r"\s+", "", code or "")


_TYPE_MAP = {"utility": "utility", "design": "design", "plant": "plant",
             "reissue": "utility"}


def map_record(rec: dict) -> Optional[dict]:
    md = rec.get("applicationMetaData") or {}
    pnum = md.get("patentNumber")
    if not pnum:
        return None
    applicants = md.get("applicantBag") or []
    a0 = applicants[0] if applicants else {}
    a0_addr = (a0.get("correspondenceAddressBag") or [{}])[0]
    inventors = []
    for iv in (md.get("inventorBag") or []):
        addr = (iv.get("correspondenceAddressBag") or [{}])[0]
        inventors.append({
            "name": iv.get("inventorNameText")
                    or " ".join(x for x in [iv.get("firstName"), iv.get("lastName")] if x).strip() or None,
            "city": addr.get("cityName"),
            "state": addr.get("geographicRegionCode") or addr.get("geographicRegionName"),
            "country": addr.get("countryCode"),
        })
    cpc_raw = md.get("cpcClassificationBag") or []
    cpc_codes = [_norm_cpc(c) for c in cpc_raw if c]
    cpc_primary = cpc_codes[0] if cpc_codes else None
    ptype = (md.get("applicationTypeLabelName") or "").strip().lower()
    grant = md.get("grantDate")
    return {
        "patent_number": str(pnum),
        "application_number": rec.get("applicationNumberText") or md.get("applicationNumberText"),
        "title": md.get("inventionTitle"),
        "abstract": None,  # not in ODP file-wrapper metadata (future XML backfill)
        "filing_date": md.get("filingDate"),
        "grant_date": grant,
        "publication_date": grant,
        "assignee_name": a0.get("applicantNameText") or md.get("firstApplicantName"),
        "assignee_country": a0_addr.get("countryCode"),
        "inventors": inventors or None,
        "cpc_codes": cpc_codes or None,
        "cpc_primary": cpc_primary,
        "claims_count": None,       # not in ODP search metadata
        "citation_count": None,     # not in ODP search metadata
        "status": "granted",
        "patent_type": _TYPE_MAP.get(ptype, ptype or None),
        "source_url": _gp_url(pnum),
    }


async def fetch_recent(date_from: str, *, max_pages: int = 20, page_size: int = 100) -> list:
    """Patents granted on/after date_from (YYYY-MM-DD). Offset-paginated."""
    if not configured():
        logger.info("PATENTSVIEW_API_KEY (USPTO ODP key) unset — skipping fetch")
        return []
    headers = {"X-API-KEY": config.PATENTSVIEW_API_KEY, "Content-Type": "application/json"}
    today = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime())
    q = f"applicationMetaData.grantDate:[{date_from} TO {today}]"
    rows: list = []
    for page in range(max_pages):
        body = {"q": q, "pagination": {"offset": page * page_size, "limit": page_size}}
        r = await request_json("POST", _ENDPOINT, headers=headers, body=body,
                               timeout=max(config.REQUEST_TIMEOUT, 60))
        if not isinstance(r, dict) or "patentFileWrapperDataBag" not in r:
            logger.warning(f"ODP page {page} error: {str(r)[:400]}")
            break
        batch = r.get("patentFileWrapperDataBag") or []
        for rec in batch:
            m = map_record(rec)
            if m:
                rows.append(m)
        total = r.get("count", 0)
        logger.info(f"ODP page {page + 1}: +{len(batch)} (total {len(rows)}/{total})")
        if len(batch) < page_size or (page + 1) * page_size >= total:
            break
    else:
        logger.warning(f"ODP paging hit max_pages={max_pages}")
    return rows
