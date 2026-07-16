from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def company_patents(
        company_name: str,
        days_back: Optional[int] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Patent portfolio for a company/assignee — total count, 90-day filing
        velocity, primary technology areas (CPC), and recent filings. Patent
        landscape + competitive IP intelligence.

        PAID: $0.01 per query after the daily free allowance (25/day). On a
        402, settle the returned payment challenge and re-call with the SAME args
        plus payment_tx=<reference>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            company_name: assignee/company name, partial match (e.g. "Qualcomm").
            days_back: optionally restrict recent filings to the last N days.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: payment transaction reference, when re-calling after a 402.
        """
        return await core.do_company(company_name, days_back,
                                     agent_key=identity.resolve_agent_key(agent_id),
                                     payment_tx=payment_tx, api_key=identity.bearer())
