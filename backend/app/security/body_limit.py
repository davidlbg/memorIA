import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.security.internal import token_for_path


class BodyLimitMiddleware:
    """Bound actual bytes before multipart parsing, including chunked uploads."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        headers = dict(scope["headers"])
        if scope["method"] != "OPTIONS":
            token = token_for_path(scope["path"])
            provided = headers.get(b"authorization", b"")
            if not token or not hmac.compare_digest(provided, f"Bearer {token}".encode()):
                await JSONResponse(
                    {"error": {"code": 401, "message": "Autenticação necessária"}},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > settings.import_max_bytes + 65536:
                await JSONResponse(
                    {"error": {"code": 413, "message": "Upload excede o limite"}}, status_code=413
                )(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
