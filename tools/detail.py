import core


def register(mcp) -> None:
    @mcp.tool
    async def patent_detail(patent_number: str) -> dict:
        """Full record for a single patent — title, abstract, inventors, assignee,
        CPC codes, claims count, citation count, filing/grant dates. FREE.

        Args:
            patent_number: the USPTO patent number from a search result.
        """
        return await core.do_detail(patent_number)
