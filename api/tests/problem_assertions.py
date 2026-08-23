from __future__ import annotations

from typing import Any
from uuid import UUID

from httpx import Response


def problem_without_request_id(response: Response) -> dict[str, Any]:
    body = response.json()
    assert isinstance(body, dict)
    request_id = body.pop("request_id")
    assert str(UUID(request_id)) == request_id
    assert response.headers["x-request-id"] == request_id
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["schema_version"] == "1.0"
    return body
