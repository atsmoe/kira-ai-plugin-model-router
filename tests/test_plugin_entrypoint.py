import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_plugin_module():
    package = types.ModuleType("model_router_plugin")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package

    core = types.ModuleType("core")
    core_chat = types.ModuleType("core.chat")
    core_plugin = types.ModuleType("core.plugin")

    class BasePlugin:
        def __init__(self, ctx, cfg):
            self.ctx = ctx
            self.plugin_cfg = cfg

    class On:
        @staticmethod
        def im_batch_message(priority=None):
            return lambda func: func

    core_chat.KiraMessageBatchEvent = object
    core_plugin.BasePlugin = BasePlugin
    core_plugin.Priority = SimpleNamespace(HIGH=50)
    core_plugin.logger = SimpleNamespace(warning=lambda _message: None)
    core_plugin.on = On()
    sys.modules["core"] = core
    sys.modules["core.chat"] = core_chat
    sys.modules["core.plugin"] = core_plugin

    spec = importlib.util.spec_from_file_location(
        "model_router_plugin.main", ROOT / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PluginEntrypointTests(unittest.TestCase):
    def test_batch_hook_routes_event_through_public_plugin_entrypoint(self):
        module = load_plugin_module()
        default = SimpleNamespace(
            model=SimpleNamespace(provider_id="provider-a", model_id="default")
        )
        context = SimpleNamespace(
            get_default_llm_client=lambda: default,
            get_llm_client=lambda model_uuid: None,
        )
        plugin = module.ModelRouterPlugin(context, {"primary_model": "default"})
        event = SimpleNamespace(model_group=[])

        asyncio.run(plugin.route_batch_message(event))

        self.assertEqual(event.model_group, [default])


if __name__ == "__main__":
    unittest.main()
