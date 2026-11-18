"""Owned ephemeral aiohttp integration; never imports or queues ComfyUI."""
import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from workflow_architect import routes
from workflow_architect.service import ArchitectService


async def main():
    service = ArchitectService()
    service.refresh(json.loads((ROOT / "tests/fixtures/comfy_0_27_local_nodes.json").read_text(encoding="utf-8")))
    table = web.RouteTableDef()
    fake_server = types.ModuleType("server")
    fake_server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=table))
    with patch.dict(sys.modules, {"server": fake_server}), patch.object(routes, "SERVICE", service), patch.object(routes, "_REGISTERED", False), patch.object(service, "refresh"):
        assert routes.register_routes()
        app = web.Application()
        app.add_routes(table)
        async with TestServer(app) as server, ClientSession() as client:
            cases = [
                ({"text": "调整图片尺寸为192x96"}, 200, "template-candidate"),
                ({"text": "调整图片尺寸为192x96", "build_draft": True}, 200, "selection-required"),
                ({"text": "调整图片尺寸为192x96", "build_draft": True, "input_overrides": {"source_image": "owned-probe.png"}}, 200, "compiled-not-executed"),
                ({"text": "Wan首尾帧视频", "build_draft": True}, 200, "reference-only"),
                ({"text": "调整图片尺寸", "build_draft": "true"}, 400, None),
                ({"text": "调整图片尺寸", "api_prompt": {}}, 400, None),
            ]
            for payload, expected_http, expected_status in cases:
                if payload.get("build_draft") is True:
                    async with client.post(server.make_url("/workflow-architect/knowledge/assist"), json={"text": payload["text"]}) as preview:
                        payload["expected"] = (await preview.json())["data"]["expected"]
                async with client.post(server.make_url("/workflow-architect/knowledge/assist"), json=payload) as response:
                    result = await response.json()
                    assert response.status == expected_http, result
                    if expected_status:
                        assert result["data"]["status"] == expected_status, result
                        assert result["data"]["execution_authorized"] is False
                        assert result["data"]["api_enabled"] is False
            for payload, status in [({}, 200), ({"execute": True}, 400), ({"build_draft": "true"}, 400)]:
                async with client.post(server.make_url("/workflow-architect/people/identity/prepare"), json=payload) as response:
                    result = await response.json()
                    assert response.status == status, result
                    if status == 200:
                        assert result["data"]["status"] == "dependency-required"
                        assert result["data"]["compiled"] is None
                        assert result["data"]["execution_authorized"] is False
            print("9/9 owned HTTP cases passed; no ComfyUI queue or external API used")


if __name__ == "__main__":
    asyncio.run(main())
