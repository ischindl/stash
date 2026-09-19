"""The shape of a share MCP URL: an origin, a prefix, and a handle.

A leaf module on purpose. `models.py` serializes a skill's MCP URL, and pulling
in the service that reads *through* those handles would drag the whole service
chain — and a circular import — into any process that only wanted a model: the
CLI test suite dies collecting with "cannot import name 'skill_mcp_url' from
partially initialized module 'share_mcp_service'". Only the URL's shape lives
here; what a handle is allowed to read lives in `share_mcp_service`.
"""

from backend.config import settings

MCP_PATH_PREFIX = "/api/v1/mcp"


def origin() -> str:
    """The origin the share dialog shows in its URLs — the product's public
    face, which proxies /api/v1 through to this backend."""
    return settings.PUBLIC_URL.rstrip("/")


def skill_mcp_url(slug: str) -> str:
    return f"{origin()}{MCP_PATH_PREFIX}/skills/{slug}"


def folder_mcp_url_for_token(token: str) -> str:
    return f"{origin()}{MCP_PATH_PREFIX}/folders/{token}"
