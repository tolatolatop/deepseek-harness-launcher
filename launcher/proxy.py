import asyncio
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request, WebSocket
from sqlmodel import Session
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse
from websockets.asyncio.client import connect

from launcher.db import engine
from launcher.security import authenticated, check_origin, decrypt
from launcher.services import own_workspace
from launcher.settings import settings

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def target(token: str | None, workspace_id: str):
    with Session(engine()) as db:
        user = authenticated(db, token)
        ws = own_workspace(db, user, workspace_id)
        if ws.status != "running" or not ws.runtime_address or not ws.runtime_cookie:
            raise HTTPException(409, "工作空间未就绪，请返回工作台启动")
        return ws.runtime_address, decrypt(ws.runtime_cookie)


def still_authorized(token: str | None, workspace_id: str, address: str) -> bool:
    try:
        return target(token, workspace_id)[0] == address
    except HTTPException:
        return False


async def proxy_http(request: Request, workspace_id: str, path: str):
    cfg = settings()
    check_origin(request)
    token = request.cookies.get("launcher_session")
    address, cookie = target(token, workspace_id)
    if "token" in request.query_params:
        raise HTTPException(400, "启动凭证由平台管理")
    if int(request.headers.get("content-length", "0") or 0) > cfg.upload_limit_mb * 1024**2:
        raise HTTPException(413, "请求过大")
    prefix = f"{cfg.base_path}/w/{workspace_id}/"
    # Never pass the launcher session or user-supplied credentials to the runtime.
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower()
        not in HOP_HEADERS
        | {
            "cookie",
            "authorization",
            "host",
            "accept-encoding",
            "x-csrf-token",
            "forwarded",
            "x-forwarded-host",
            "x-forwarded-for",
            "x-forwarded-proto",
        }
    }
    headers.update({"Host": urlsplit(cfg.public_url).netloc, "Cookie": cookie, "Accept-Encoding": "identity"})
    query = request.url.query
    url = f"{address}/{path}" + (f"?{query}" if query else "")
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30, read=None), follow_redirects=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=request.stream()), stream=True
        )
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(502, "工作空间暂时无法连接，请返回工作台检查状态") from None
    response_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in HOP_HEADERS | {"set-cookie", "content-length", "content-encoding"}
    }
    location = response_headers.get("location")
    if location:
        parsed = urlsplit(location)
        if parsed.query and "token" in dict(httpx.QueryParams(parsed.query)):
            response_headers.pop("location", None)
        elif location.startswith("/") and not location.startswith("//"):
            response_headers["location"] = prefix + location.lstrip("/")
        elif parsed.netloc:
            response_headers.pop("location", None)
    response_headers["cache-control"] = "no-store"
    response_headers["x-content-type-options"] = "nosniff"
    response_headers["referrer-policy"] = "no-referrer"

    async def close():
        await upstream.aclose()
        await client.aclose()

    async def body():
        iterator = upstream.aiter_bytes()
        pending = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(anext(iterator))
                done, _ = await asyncio.wait([pending], timeout=3)
                if not still_authorized(token, workspace_id, address):
                    break
                if not done:
                    continue
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                yield chunk
        finally:
            if pending and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await close()

    if request.method == "HEAD":
        await close()
        return Response(status_code=upstream.status_code, headers=response_headers)
    return StreamingResponse(
        body(), status_code=upstream.status_code, headers=response_headers, background=BackgroundTask(close)
    )


async def proxy_ws(websocket: WebSocket, workspace_id: str, path: str):
    cfg = settings()
    token = websocket.cookies.get("launcher_session")
    if websocket.headers.get("origin") != cfg.origin:
        await websocket.close(code=1008)
        return
    try:
        address, cookie = target(token, workspace_id)
    except HTTPException:
        await websocket.close(code=1008)
        return
    endpoint = urlsplit(address)
    query = websocket.url.query
    url = f"ws://{urlsplit(cfg.public_url).netloc}/{path}" + (f"?{query}" if query else "")
    protocols = [
        v.strip() for v in websocket.headers.get("sec-websocket-protocol", "").split(",") if v.strip()
    ]
    try:
        async with connect(
            url,
            host=endpoint.hostname,
            port=endpoint.port,
            proxy=None,
            origin=cfg.origin,
            additional_headers={"Cookie": cookie},
            subprotocols=protocols or None,
            max_size=16 * 1024**2,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def to_runtime():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    await upstream.send(
                        message.get("text") if message.get("text") is not None else message["bytes"]
                    )

            async def to_browser():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            async def monitor():
                while still_authorized(token, workspace_id, address):
                    await asyncio.sleep(2)

            tasks = [asyncio.create_task(fn()) for fn in (to_runtime, to_browser, monitor)]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        # Runtime errors may include credentials in headers; never log their repr.
        pass
    finally:
        try:
            await websocket.close(code=1000)
        except RuntimeError:
            pass
