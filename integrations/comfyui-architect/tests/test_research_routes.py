"""Route-handler tests with a fake server; no installed Comfy/aiohttp required."""
import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from workflow_architect import routes
from workflow_architect.service import ArchitectService


class FakeRoutes:
    def __init__(self):
        self.handlers = {}

    def _register(self, method, path):
        def decorate(handler):
            self.handlers[(method, path)] = handler
            return handler
        return decorate

    def get(self, path):
        return self._register("GET", path)

    def post(self, path):
        return self._register("POST", path)


class ResearchRouteTests(unittest.TestCase):
    def _run(self, fail=False):
        registry = FakeRoutes()
        web = types.SimpleNamespace(json_response=lambda body, status=200: (status, body))
        prompt_server = types.SimpleNamespace(instance=types.SimpleNamespace(routes=registry))
        modules = {"aiohttp": types.SimpleNamespace(web=web),
                   "server": types.SimpleNamespace(PromptServer=prompt_server)}
        service = ArchitectService()
        if fail:
            service.research_catalog = lambda: (_ for _ in ()).throw(ValueError("PRIVATE_PATH_SECRET"))
        with patch.dict(sys.modules, modules), patch.object(routes, "SERVICE", service), \
                patch.object(routes, "_REGISTERED", False), patch.object(service, "refresh", return_value={}):
            self.assertTrue(routes.register_routes())
            self.assertTrue(routes.register_routes())
            response = asyncio.run(registry.handlers[("GET", "/workflow-architect/research")](None))
        return registry.handlers, response

    def test_research_get_only_and_non_executable(self):
        handlers, (status, body) = self._run()
        self.assertNotIn(("POST", "/workflow-architect/research"), handlers)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIs(body["data"]["execution_authorized"], False)
        self.assertEqual(len(body["data"]["recipes"]), 17)

    def test_corrupt_metadata_returns_fixed_error(self):
        _, (status, body) = self._run(fail=True)
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("PRIVATE_PATH_SECRET", str(body))
