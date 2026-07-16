"""patent-intel-mcp tools — one per file.

  search_patents       ($0.01)  filtered patent search
  patent_detail        (free)   full record — drives adoption
  company_patents      ($0.01)  portfolio / filing velocity / tech areas
  trending_technology  ($0.01)  CPC classes by filing volume
  prior_art_search     ($0.02)  pgvector semantic similarity
  daily_digest         ($0.02)  structured daily filing digest
  daily_brief          ($10)    curated daily patent-intel brief (premium)
  mint_info            (free)   FoundryNet Data Network info
"""
from . import search as search_tool
from . import detail as detail_tool
from . import company as company_tool
from . import trending as trending_tool
from . import prior_art as prior_art_tool
from . import digest as digest_tool
from . import daily_brief as daily_brief_tool
from . import brief_summary as brief_summary_tool
from . import mint as mint_tool


def register_all(mcp) -> None:
    search_tool.register(mcp)
    detail_tool.register(mcp)
    company_tool.register(mcp)
    trending_tool.register(mcp)
    prior_art_tool.register(mcp)
    digest_tool.register(mcp)
    daily_brief_tool.register(mcp)
    brief_summary_tool.register(mcp)
    mint_tool.register(mcp)
