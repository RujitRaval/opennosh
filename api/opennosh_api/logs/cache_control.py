from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PRIVATE_PATHS = ("/api/v1/logs", "/api/v1/recipes", "/api/v1/targets")


class FoodLogNoStoreMiddleware:
    """Prevent caches from retaining successful or failed private nutrition data."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path")
        if (
            scope["type"] != "http"
            or not isinstance(path, str)
            or not any(path == root or path.startswith(f"{root}/") for root in _PRIVATE_PATHS)
        ):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)
