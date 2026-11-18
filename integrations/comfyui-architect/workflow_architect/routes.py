from __future__ import annotations

import logging
import asyncio
import threading
from typing import Any, Awaitable, Callable

from . import __version__
from .errors import WorkflowArchitectError
from .service import ArchitectService
from .settings import API_BODY_LIMIT

LOGGER = logging.getLogger("workflow_architect")
SERVICE = ArchitectService()
_REGISTERED = False


def register_routes() -> bool:
    global _REGISTERED
    if _REGISTERED:
        return True
    try:
        from aiohttp import web
        from server import PromptServer  # type: ignore
    except (ImportError, AttributeError):
        return False

    instance = getattr(PromptServer, "instance", None)
    if instance is None or not hasattr(instance, "routes"):
        return False
    routes = instance.routes

    @routes.get("/workflow-architect/health")
    async def health(request):
        del request
        return web.json_response(
            _ok(
                {
                    "name": "AI 工作流架构师",
                    "version": __version__,
                    "offline": True,
                    "supported_tasks": [
                        "text_to_image",
                        "image_to_image",
                        "inpaint",
                        "image_resize",
                        "image_upscale",
                        "virtual_tryon",
                        "ecommerce_detail",
                    ],
                    "registry_generation": SERVICE.registry.current().generation,
                }
            )
        )

    @routes.get("/workflow-architect/capabilities")
    async def capabilities(request):
        del request
        return web.json_response(_ok(SERVICE.registry.current().to_dict()))

    @routes.get("/workflow-architect/manager/status")
    async def manager_status(request):
        del request
        return web.json_response(_ok(SERVICE.manager_status()))

    @routes.get("/workflow-architect/research")
    async def research_catalog(request):
        del request
        try:
            return web.json_response(_ok(SERVICE.research_catalog()))
        except Exception as exc:
            return web.json_response(_exception_payload(exc), status=400)

    _post(routes, web, "/workflow-architect/parse", SERVICE.parse)
    _post(routes, web, "/workflow-architect/plan", SERVICE.plan)
    _post(routes, web, "/workflow-architect/compile", SERVICE.compile)
    _post(routes, web, "/workflow-architect/validate", SERVICE.validate)
    _post(routes, web, "/workflow-architect/knowledge/assist", SERVICE.knowledge_assist)
    from .prompt_library import search as prompt_search, render as prompt_render
    _post(routes, web, "/workflow-architect/prompts/search", prompt_search, strict_limit=16384)
    _post(routes, web, "/workflow-architect/prompts/render", prompt_render, strict_limit=16384)
    from .offline_ai import status as offline_status, assist as offline_assist
    _post(routes, web, '/workflow-architect/offline/status', offline_status, strict_limit=4096)
    @routes.post('/workflow-architect/offline/assist')
    async def offline_request(request):
        if request.remote not in ('127.0.0.1','::1') or request.headers.get('Origin') not in ('http://127.0.0.1:8188','http://localhost:8188',None):
            return web.json_response(_error('FORBIDDEN','仅本机同源可调用本地模型'),status=403)
        if request.content_type!='application/json' or not request.content_length or request.content_length>8192:
            return web.json_response(_error('INVALID_REQUEST','本地助手须为8KiB以内完整JSON'),status=400)
        cancel=threading.Event()
        try:
            payload=await request.json()
            task=asyncio.create_task(asyncio.to_thread(offline_assist,SERVICE,payload,cancel))
            task.add_done_callback(lambda t:None if t.cancelled() else t.exception())
            result=await asyncio.wait_for(asyncio.shield(task),122)
            return web.json_response(_ok(result),headers={'Cache-Control':'private, no-store'})
        except asyncio.CancelledError:
            cancel.set();raise
        except asyncio.TimeoutError:
            cancel.set();return web.json_response(_error('TIMEOUT','本地推理到期，不自动重试，不接受迟到结果'),status=408)
        except Exception as exc:
            cancel.set();return web.json_response(_exception_payload(exc),status=400)
    _post(routes, web, "/workflow-architect/people/tryon/prepare", SERVICE.prepare_tryon)
    _post(routes, web, "/workflow-architect/foundation/report", SERVICE.foundation_report)
    _post(routes, web, "/workflow-architect/planner/context", SERVICE.planning_context)
    _post(routes, web, "/workflow-architect/planner/validate", SERVICE.validate_proposal)
    _post(routes, web, "/workflow-architect/planner/compile", SERVICE.compile_plan)
    _post(routes, web, "/workflow-architect/api/status", SERVICE.api_support)
    _post(routes, web, "/workflow-architect/api/profile-check", SERVICE.api_profile_check)
    _post(routes, web, "/workflow-architect/detail/context", SERVICE.detail_context)
    _post(routes, web, "/workflow-architect/detail/validate", SERVICE.detail_validate)
    _post(routes, web, "/workflow-architect/detail/compile", SERVICE.detail_compile)
    @routes.post('/workflow-architect/poster/options')
    async def poster_options(request):
        if request.remote not in ('127.0.0.1','::1') or request.headers.get('Origin') not in (None,'http://127.0.0.1:8188','http://localhost:8188'):
            return web.json_response(_error('FORBIDDEN','仅允许本机同源素材读取'),status=403)
        if request.content_type!='application/json' or not request.content_length or request.content_length>4096:
            return web.json_response(_error('INVALID_REQUEST','须为空对象JSON'),status=400)
        try:return web.json_response(_ok(SERVICE.poster_options(await request.json())))
        except Exception as exc:return web.json_response(_exception_payload(exc),status=400)

    @routes.post('/workflow-architect/poster/prepare')
    async def poster_prepare(request):
        if request.remote not in ('127.0.0.1','::1') or request.headers.get('Origin') not in (None,'http://127.0.0.1:8188','http://localhost:8188'):
            return web.json_response(_error('FORBIDDEN','仅允许本机同源海报准备'),status=403)
        if request.content_type!='application/json' or not request.content_length or request.content_length>16384:
            return web.json_response(_error('INVALID_REQUEST','须为有界JSON'),status=400)
        cancel=threading.Event()
        try:
            payload=await request.json()
            task=asyncio.create_task(asyncio.to_thread(SERVICE.poster_prepare,payload,cancel))
            task.add_done_callback(lambda t:None if t.cancelled() else t.exception())
            value=await asyncio.wait_for(asyncio.shield(task),31)
            return web.json_response(_ok(value),headers={'Cache-Control':'no-store'})
        except asyncio.CancelledError:
            cancel.set();raise
        except asyncio.TimeoutError:
            cancel.set();return web.json_response(_error('TIMEOUT','准备到期；不会自动排队或重试，可能保留自有暂存副本'),status=408)
        except Exception as exc:
            cancel.set();return web.json_response(_exception_payload(exc),status=400)

    @routes.get('/workflow-architect/poster/package/{ident}')
    async def poster_package(request):
        from .poster_service import package_path
        try:
            if request.remote not in ('127.0.0.1','::1') or request.headers.get('Origin') not in (None,'http://127.0.0.1:8188','http://localhost:8188'):
                return web.json_response(_error('FORBIDDEN','仅允许本机同源海报包下载'),status=403)
            if request.query:raise WorkflowArchitectError('海报包不接受查询参数')
            path=package_path(request.match_info['ident'])
            return web.FileResponse(path,headers={'Content-Type':'application/zip','Content-Disposition':'attachment; filename="'+path.name+'"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
        except Exception as exc:return web.json_response(_exception_payload(exc),status=400)

    @routes.post('/workflow-architect/detail/propose')
    @routes.post('/workflow-architect/api/propose')
    async def api_propose(request):
        # No HTTP config/key installation. Native trusted host must wire provider.
        if request.remote not in ('127.0.0.1', '::1') or request.headers.get('Origin') not in (None, 'http://127.0.0.1:8188', 'http://localhost:8188'):
            return web.json_response(_error('FORBIDDEN', '仅允许本机同源规划确认'), status=403)
        if request.content_type != 'application/json' or not request.content_length or request.content_length > 16384:
            return web.json_response(_error('INVALID_REQUEST', '须为有界JSON请求'), status=400)
        cancel = threading.Event()
        try:
            payload = await request.json()
            handler=SERVICE.detail_propose if request.path.endswith('/detail/propose') else SERVICE.propose_api
            task = asyncio.create_task(asyncio.to_thread(handler, payload, cancel))
            def observe(done):
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(observe)
            value = await asyncio.wait_for(asyncio.shield(task), timeout=31)
            return web.json_response(_ok(value), headers={'Cache-Control': 'no-store'})
        except asyncio.CancelledError:
            cancel.set()
            raise
        except asyncio.TimeoutError:
            cancel.set()
            return web.json_response(_error('TIMEOUT', 'API到期；若已发送费用可能未知，不自动重试'), status=408)
        except Exception as exc:
            cancel.set()
            return web.json_response(_exception_payload(exc), status=400)

    @routes.post("/workflow-architect/refresh")
    async def refresh(request):
        if request.content_length and request.content_length > API_BODY_LIMIT:
            return web.json_response(_error("PAYLOAD_TOO_LARGE", "请求体过大"), status=413)
        try:
            return web.json_response(_ok(SERVICE.refresh()))
        except Exception as exc:  # ComfyUI must survive third-party node failures.
            LOGGER.warning("Capability refresh failed: %s", type(exc).__name__)
            return web.json_response(_exception_payload(exc), status=400)

    # Populate once after ComfyUI has loaded its NODE_CLASS_MAPPINGS.
    try:
        SERVICE.refresh()
    except Exception as exc:
        LOGGER.warning("Initial capability scan failed: %s", type(exc).__name__)
    _REGISTERED = True
    return True


def _post(routes, web, path: str, handler: Callable[[dict[str, Any]], Any], *, strict_limit=None) -> None:
    @routes.post(path)
    async def endpoint(request):
        if strict_limit is not None and (request.content_type != 'application/json' or not request.content_length or request.content_length > strict_limit):
            return web.json_response(_error('INVALID_REQUEST','提示词接口须为16KiB以内完整JSON'),status=400)
        if request.content_length and request.content_length > API_BODY_LIMIT:
            return web.json_response(_error("PAYLOAD_TOO_LARGE", "请求体过大"), status=413)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise WorkflowArchitectError("请求 JSON 必须是对象")
            return web.json_response(_ok(handler(payload)), **({'headers':{'Cache-Control':'private, no-store'}} if strict_limit is not None else {}))
        except Exception as exc:
            LOGGER.info("Request rejected at %s: %s", path, type(exc).__name__)
            return web.json_response(_exception_payload(exc), status=400)


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _error(code: str, message: str, details: dict | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _exception_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, WorkflowArchitectError):
        return _error(exc.code, exc.message, exc.details)
    return _error("INTERNAL_ERROR", "处理请求时发生错误；请检查 ComfyUI 控制台")
