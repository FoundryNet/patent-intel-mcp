from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def trending_technology(
        days: int = 30,
        min_filings: int = 1,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Which technology areas are heating up — CPC classes ranked by recent
        patent filing volume, each with a section description and its top assignees.
        Technology-trend and patent-landscape signal.

        PAID: $0.01 per query after the daily free allowance (25/day). On a
        402, settle the returned payment challenge and re-call with the SAME args
        plus payment_tx=<reference>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            days: look-back window in days (1-365, default 30).
            min_filings: only include CPC classes with at least this many filings.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: payment transaction reference, when re-calling after a 402.
        """
        return await core.do_trending(days, min_filings,
                                      agent_key=identity.resolve_agent_key(agent_id),
                                      payment_tx=payment_tx, api_key=identity.bearer())
