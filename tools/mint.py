import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_info() -> dict:
        """FoundryNet Data Network info + MINT Protocol details. FREE.

        Returns how to attest your agent's patent research with MINT Protocol for
        verifiable on-chain proof, the MINT MCP endpoint, and the sister data
        servers (gov-contracts-mcp, brand-intel-mcp).
        """
        return core.mint_info()
