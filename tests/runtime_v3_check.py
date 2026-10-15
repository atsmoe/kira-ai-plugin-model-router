"""Opt-in v3 integration: real core classes, synthetic providers, no network.

Run separately from the stub-based unit suite, with KIRA_CORE_SOURCE pointing to
a trusted v3 checkout and that checkout's Python dependencies installed.
"""

import asyncio
import atexit
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

CORE = Path(os.environ["KIRA_CORE_SOURCE"]).resolve()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
sys.path.insert(1, str(ROOT))

# Keep every core log/config path outside the running service's data directory.
from core.utils.path_utils import init_paths
_data = tempfile.TemporaryDirectory(prefix="router-v3-acceptance-")
init_paths(data_dir=_data.name)

def cleanup():
    import logging
    logging.shutdown()
    _data.cleanup()

atexit.register(cleanup)

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from core.agent.agent_executor import AgentExecutionContext, AgentExecutor
from core.chat import KiraMessageBatchEvent
from core.plugin import PluginContext
from core.plugin import registry
from core.plugin.handlers import EventType, Priority
from core.provider import LLMModelClient, LLMRequest, LLMResponse, ProviderAPIError
from core.provider.model_identity import MODEL_CONFIG_VERSION
from core.provider.provider import ModelType
from core.provider.provider_manager import ProviderManager
from router import ModelRouter


class Config(dict):
    def get_config(self, key):
        value = self
        for part in key.split("."):
            value = value.get(part, {})
        return value


class SyntheticClient(LLMModelClient):
    calls = []
    errors = {}

    async def chat(self, request, **kwargs):
        self.calls.append(self.model.model_id)
        error = self.errors.get(self.model.model_id)
        if error:
            raise error
        return LLMResponse("synthetic-accepted")


class RuntimeV3Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        SyntheticClient.calls = []
        SyntheticClient.errors = {}
        providers = {}
        for identity, name in (("primary", "Primary"), ("backup", "Backup")):
            providers[identity] = {
                "name": name, "status": "active",
                "model_config_version": MODEL_CONFIG_VERSION,
                "model_config": {"llm": {f"{identity}-stable": {
                    "model_name": f"{identity}:upstream", "config": {},
                    "legacy_ids": [f"{identity}-legacy"],
                }}},
            }
        self.manager = object.__new__(ProviderManager)
        self.manager.kira_config = Config(providers=providers,
                                         models={"default_llm": "primary:primary-stable"})
        self.manager._providers = {
            identity: NS(provider_id=identity, provider_name=config["name"],
                         models={ModelType.LLM: SyntheticClient})
            for identity, config in providers.items()
        }
        self.context = object.__new__(PluginContext)
        self.context.provider_mgr = self.manager

    def event(self):
        return KiraMessageBatchEvent(supported_elements=["text"], timestamp=0,
                                    session=NS(sid="acceptance@internal.dm"))

    def route(self, config=None):
        event = self.event()
        ModelRouter(self.context, config or {
            "primary_model": "Primary：primary:upstream",
            "fallback_models": ["backup:backup:upstream", "backup:backup-legacy", "backup:backup-stable"],
        }).route(event)
        return event

    async def execute(self, event):
        context = AgentExecutionContext(event=event, request=LLMRequest(),
                                        new_messages=[], model_group=event.model_group)
        return [result async for result in AgentExecutor(tool_manager=None).run(context, max_steps=2)]

    async def test_native_context_resolves_names_ids_and_legacy_aliases(self):
        event = self.route()
        self.assertEqual([m.model.model_id for m in event.model_group],
                         ["primary-stable", "backup-stable"])
        self.assertEqual([m.model.model_name for m in event.model_group],
                         ["primary:upstream", "backup:upstream"])
        self.assertIsInstance(self.context.get_llm_client("backup:backup-legacy"), LLMModelClient)

    async def test_disabled_provider_does_not_block_backup(self):
        self.manager.kira_config["providers"]["primary"]["status"] = "disabled"
        event = self.route()
        self.assertEqual([m.model.model_id for m in event.model_group], ["backup-stable"])
        self.assertEqual((await self.execute(event))[0].state, "success")

    async def test_missing_default_does_not_block_backup(self):
        self.manager.kira_config["models"] = {}
        event = self.route({"primary_model": "default", "fallback_models": ["backup:backup-legacy"]})
        self.assertEqual([m.model.model_id for m in event.model_group], ["backup-stable"])

    async def test_disabled_provider_id_cannot_become_another_providers_name(self):
        self.manager.kira_config["providers"]["primary"]["status"] = "disabled"
        self.manager._providers.pop("primary")
        self.manager._providers["backup"].provider_name = "primary"
        event = self.route({"primary_model": "primary:backup-stable", "fallback_models": []})
        self.assertEqual(event.model_group, [])

    async def test_core_failover_for_supported_provider_errors(self):
        request = httpx.Request("POST", "https://synthetic.invalid")
        errors = [ProviderAPIError("synthetic"), APIConnectionError(request=request),
                  APITimeoutError(request=request),
                  APIStatusError("synthetic", response=httpx.Response(503, request=request), body=None)]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                SyntheticClient.calls = []
                SyntheticClient.errors = {"primary-stable": error}
                results = await self.execute(self.route())
                self.assertEqual(SyntheticClient.calls, ["primary-stable", "backup-stable"])
                self.assertEqual(results[0].state, "success")
                self.assertEqual(results[0].model_id, "backup-stable")
                self.assertEqual(results[0].assistant_message.content, "synthetic-accepted")

    async def test_provider_disabled_after_routing_fails_over(self):
        event = self.route()
        self.manager.kira_config["providers"]["primary"]["status"] = "disabled"
        results = await self.execute(event)
        self.assertEqual(SyntheticClient.calls, ["backup-stable"])
        self.assertEqual(results[0].model_id, "backup-stable")
        self.assertEqual(results[0].state, "success")

    async def test_all_models_failed_keeps_core_error_result(self):
        SyntheticClient.errors = {key: ProviderAPIError("synthetic")
                                  for key in ("primary-stable", "backup-stable")}
        result = (await self.execute(self.route()))[0]
        self.assertEqual(result.state, "error")
        self.assertTrue(result.is_final)

    async def test_programming_error_is_not_hidden_by_failover(self):
        SyntheticClient.errors = {"primary-stable": ValueError("synthetic")}
        with self.assertRaisesRegex(ValueError, "synthetic"):
            await self.execute(self.route())
        self.assertEqual(SyntheticClient.calls, ["primary-stable"])

    async def test_actual_entrypoint_registers_low_priority_batch_hook(self):
        package = "model_router_v3_acceptance"
        spec = importlib.util.spec_from_file_location(package, ROOT / "main.py",
                                                     submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
        plugin_id = "kira-ai-plugin-model-router"
        component = registry._plugin_components[plugin_id]
        hooks = [hook for hook in component.hooks if hook.event_type == EventType.ON_IM_BATCH_MESSAGE]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0].priority, Priority.LOW)
        plugin = module.ModelRouterPlugin(self.context, {"fallback_models": ["backup:backup:upstream"]})
        await plugin.initialize()
        try:
            event = self.event()
            upstream = self.context.get_llm_client("primary:primary-stable")
            event.model_group = [upstream]
            # Bind the actual hook exactly as the core plugin manager does.
            hooks[0].handler = getattr(plugin, hooks[0].handler.__name__)
            await hooks[0].exec_handler(event)
            self.assertEqual([m.model.model_id for m in event.model_group], ["primary-stable", "backup-stable"])
            self.assertIs(event.model_group[0], upstream)
        finally:
            await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
