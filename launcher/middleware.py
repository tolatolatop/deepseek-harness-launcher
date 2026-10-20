from fastapi import HTTPException
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    """Enforce a receive-side bound before multipart data can spool to disk."""

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope.get("headers", [])).get(b"content-length", b"0")
        try:
            too_large = int(length) > self.limit
        except ValueError:
            return await JSONResponse({"detail": "Content-Length 无效"}, status_code=400)(
                scope, receive, send
            )
        if too_large:
            return await JSONResponse({"detail": "请求超过上传大小限制"}, status_code=413)(
                scope, receive, send
            )
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise HTTPException(413, "请求超过上传大小限制")
            return message

        await self.app(scope, bounded_receive, send)
