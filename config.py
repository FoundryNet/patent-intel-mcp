"""Env-driven configuration for patent-intel-mcp.

Patent intelligence over USPTO PatentsView data, cached in its OWN standalone
Supabase project, with pgvector semantic prior-art search. Six paid/free tools +
a free mint_info cross-promo tool. x402 (USDC on Solana) with a daily free tier.

Required to be useful:
  SUPABASE_URL, SUPABASE_SERVICE_KEY   the standalone patent-intel project.
  PATENTSVIEW_API_KEY                  free key for search.patentsview.org (the
                                       aggregator no-ops without it).
Optional:
  PORT, REQUEST_TIMEOUT
  X402_ENABLED, SOLANA_WALLET, PAYMENT_RECIPIENT, PAYMENT_VERIFY_RPC,
  PAYMENT_USDC_MINT, PAYMENT_EXPIRY_SECONDS
  FREE_TIER_DAILY            default 25
  EMBED_MODEL                fastembed model, default BAAI/bge-small-en-v1.5 (384d)
  AGG_HOUR_UTC               daily aggregation hour (UTC), default 12 (~5am PT)
  PATENTS_LOOKBACK_DAYS      cold-start window, default 1
  PRICE_*                    per-tool USDC prices (see below)
"""
from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


# ── Standalone patent-intel Supabase ─────────────────────────────────────────
SUPABASE_URL         = _env("SUPABASE_URL", "https://llehduhnveasudkgupwa.supabase.co").rstrip("/")
SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY")

PORT            = int(_env("PORT", "8080"))
REQUEST_TIMEOUT = int(_env("REQUEST_TIMEOUT", "30"))

# ── Data source ──────────────────────────────────────────────────────────────
PATENTSVIEW_API_KEY = _env("PATENTSVIEW_API_KEY")
PATENTSVIEW_URL     = _env("PATENTSVIEW_URL", "https://search.patentsview.org/api/v1/patent/").rstrip("/")
PATENTS_LOOKBACK_DAYS = int(_env("PATENTS_LOOKBACK_DAYS", "1"))
AGG_HOUR_UTC        = int(_env("AGG_HOUR_UTC", "12"))   # ~05:00 America/Los_Angeles

# ── Embeddings (fastembed, local, no key) ────────────────────────────────────
EMBED_MODEL = _env("EMBED_MODEL", "BAAI/bge-small-en-v1.5")  # 384 dims
EMBED_DIM   = int(_env("EMBED_DIM", "384"))

# ── x402 per-tool pricing ────────────────────────────────────────────────────
X402_ENABLED      = _flag("X402_ENABLED", True)
SOLANA_WALLET     = _env("SOLANA_WALLET", "wUumjWWvtFEr69qkTw3wHNVQVxLA8DTyJSyVgGmLThd")
PAYMENT_RECIPIENT = _env("PAYMENT_RECIPIENT", SOLANA_WALLET).strip()
PAYMENT_VERIFY_RPC = _env("PAYMENT_VERIFY_RPC", "https://api.mainnet-beta.solana.com").rstrip("/")
PAYMENT_USDC_MINT  = _env("PAYMENT_USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").strip()
PAYMENT_EXPIRY_SECONDS = int(_env("PAYMENT_EXPIRY_SECONDS", "300"))

FREE_TIER_DAILY = int(_env("FREE_TIER_DAILY", "25"))

PRICE_SEARCH_PATENTS     = float(_env("PRICE_SEARCH_PATENTS", "0.01"))
PRICE_COMPANY_PATENTS    = float(_env("PRICE_COMPANY_PATENTS", "0.01"))
PRICE_TRENDING_TECH      = float(_env("PRICE_TRENDING_TECH", "0.01"))
PRICE_PRIOR_ART          = float(_env("PRICE_PRIOR_ART", "0.02"))
PRICE_DAILY_DIGEST       = float(_env("PRICE_DAILY_DIGEST", "0.02"))

# ── FoundryNet Data Network cross-promotion ──────────────────────────────────
MINT_MCP_URL   = _env("MINT_MCP_URL", "https://mint-mcp-production.up.railway.app/mcp")
MINT_INFO_URL  = _env("MINT_INFO_URL", "https://mint.foundrynet.io")
SISTER_SERVERS = {
    "gov-contracts-mcp": "https://gov-contracts-mcp-production.up.railway.app/mcp",
    "brand-intel-mcp":   "https://brand-intel-mcp-production.up.railway.app/mcp",
}

PUBLIC_MCP_URL = _env("PUBLIC_MCP_URL", "https://patent-intel-mcp-production.up.railway.app/mcp")
