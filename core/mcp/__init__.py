# `core.mcp` is deliberately NOT a top-level `mcp/` package: the MCP SDK owns
# the `mcp` import name on PyPI, and a top-level package of that name shadows
# it for the whole repo (the MarketGraph lesson). As a subpackage, `import mcp`
# inside these modules still resolves the SDK under absolute imports.
