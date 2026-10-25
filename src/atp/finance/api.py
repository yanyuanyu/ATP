"""Local demonstration API. Bind to loopback; contains simulated financial data."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from atp.finance.hub import Hub
from atp.finance.ledger import BUYER, CASHIER, FinanceError
from atp.finance.messaging import Mailbox
from atp.security.sm2 import SM2PublicKey

hub = None
task = None


async def start():
    global hub, task
    public = SM2PublicKey(Path(os.environ["ATP_MERCHANT_PUBLIC"]).read_text().strip())
    hub = Hub(Path(os.environ.get("ATP_FINANCE_DATA", "./finance-state/user")), public,
              Mailbox(BUYER, os.environ.get("ATP_FINANCE_SERVER", "server-family.family.test:7443")))
    task = asyncio.create_task(hub.loop())


async def stop():
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def dispatch(request: Request):
    if hub is None:
        return JSONResponse({"error": "金融演示服务尚未初始化"}, status_code=503)
    # Only same-origin browser actions are accepted; CLI calls without Origin
    # remain available for reproducible local acceptance scripts.
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        return JSONResponse({"error": "不接受跨站操作"}, status_code=403)
    try:
        if request.method == "GET":
            response = JSONResponse(hub.snapshot())
            response.headers["Cache-Control"] = "no-store"
            return response
        if int(request.headers.get("content-length", "0")) > 4096:
            return JSONResponse({"error": "请求过大"}, status_code=413)
        body = await request.json()
        if not isinstance(body, dict):
            raise FinanceError("INVALID_REQUEST", "请求须为JSON对象")
        async with hub.lock:
            action = body.get("action")
            if action == "create":
                result = await hub.create(body.get("quantity", 2))
            elif action == "pay":
                if body.get("authorized") is not True:
                    raise FinanceError("AUTHORIZATION_REQUIRED", "请明确授权模拟支付")
                result = await hub.pay(body.get("order_id"), "pay")
            elif action == "test":
                variant = body.get("variant")
                if variant not in ("amount", "payee", "signature", "unauthorized", "repeat"):
                    raise FinanceError("INVALID_OPERATION", "未知测试类型")
                result = await hub.pay(body.get("order_id"), variant)
            elif action == "statement":
                result = {"nonce": await hub.send(CASHIER, "finance.statement", {})}
            else:
                raise FinanceError("INVALID_OPERATION", "未知操作")
        return JSONResponse({"result": result}, status_code=202)
    except FinanceError as exc:
        return JSONResponse({"error": str(exc), "code": exc.code}, status_code=400)
    except (json.JSONDecodeError, ValueError, TypeError):
        return JSONResponse({"error": "请求格式无效", "code": "INVALID_REQUEST"}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": "ATP服务暂不可用，请查看运行状态", "code": type(exc).__name__}, status_code=503)


routes = [Route("/api/finance/state", dispatch, methods=["GET"]), Route("/api/finance/action", dispatch, methods=["POST"])]


@asynccontextmanager
async def lifespan(app):
    await start()
    try:
        yield
    finally:
        await stop()


static = Path(os.environ.get("ATP_FINANCE_STATIC", str(Path(__file__).resolve().parents[3] / "demo/docker/demo/static")))


async def index(request):
    return FileResponse(static / "index.html")


app = Starlette(routes=[*routes, Route("/", index)], lifespan=lifespan)
if static.exists():
    app.mount("/static", StaticFiles(directory=str(static)))
