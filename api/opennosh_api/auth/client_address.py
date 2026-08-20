from hmac import compare_digest
from ipaddress import ip_address

from fastapi import Request

from opennosh_api.settings import Settings

PROXY_CLIENT_HEADER = "x-opennosh-client-address"
PROXY_TOKEN_HEADER = "x-opennosh-proxy-token"


def client_address(request: Request, settings: Settings) -> str:
    """Return a fair rate-limit key without trusting caller-supplied proxy headers."""

    configured_token = settings.trusted_web_proxy_token
    supplied_token = (
        request.headers.get(PROXY_TOKEN_HEADER) if "headers" in request.scope else None
    )
    supplied_client = (
        request.headers.get(PROXY_CLIENT_HEADER) if "headers" in request.scope else None
    )
    if configured_token is not None and supplied_token and supplied_client:
        if compare_digest(configured_token.get_secret_value(), supplied_token):
            try:
                canonical_client = str(ip_address(supplied_client))
            except ValueError:
                pass
            else:
                return canonical_client

    return request.client.host if request.client is not None else "unknown"
