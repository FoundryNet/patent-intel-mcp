from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def daily_brief(
        date: Optional[str] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
        stripe_token: Optional[str] = None,
    ) -> dict:
        """The curated daily patent-intel brief — the day's most significant patent
        activity in one package: the most significant recent filings, filing-velocity
        anomalies (assignees with unusual recent filing spikes), trending CPC
        classification codes, and major assignee activity. Each brief carries a
        provenance attestation so a buyer can verify it was produced by this server,
        unaltered.

        PAID: $10 per brief. Defaults to today (UTC); a brief expires at the
        next midnight UTC. On a 402, settle the returned payment challenge and
        re-call with the SAME args plus payment_tx=<reference>. An Authorization:
        Bearer fnet_ key bypasses payment.

        Args:
            date: brief date YYYY-MM-DD (default today, UTC).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: payment transaction reference, when re-calling after a 402.
            stripe_token: Stripe Checkout Session id (cs_…), when re-calling after
                paying the Stripe payment link (alternative to x402). Can also be
                supplied via the X-Stripe-Token header.
        """
        return await core.do_daily_brief(date, agent_key=identity.resolve_agent_key(agent_id),
                                         payment_tx=payment_tx, api_key=identity.bearer(),
                                         stripe_token=stripe_token or identity.stripe_token())
