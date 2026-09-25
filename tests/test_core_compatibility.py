"""Optional source-level checks; set KIRA_CORE_SOURCE to a trusted KiraAI checkout.

Execute selected, unmodified core definitions with synthetic dependencies. These
checks do not start KiraAI, load providers, or exercise a live chat service.
"""

import ast
import asyncio
import json
import os
import types
import unittest
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from router import ModelRouter


CORE_SOURCE = os.environ.get("KIRA_CORE_SOURCE")
ROOT = Path(__file__).resolve().parents[1]


def definition(path, name, namespace, owner=None):
    tree = ast.parse((Path(CORE_SOURCE) / path).read_text(encoding="utf-8"))
    body = tree.body
    if owner:
        body = next(node for node in body if isinstance(node, ast.ClassDef) and node.name == owner).body
    node = next(node for node in body if getattr(node, "name", None) == name)
    if owner:
        node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, path, "exec"), namespace)
    return namespace[name]


@unittest.skipUnless(CORE_SOURCE, "Set KIRA_CORE_SOURCE for optional real-core source checks")
class CoreCompatibilityTests(unittest.TestCase):
    def test_actual_core_version_checker_accepts_lower_bound_without_upper_bound(self):
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version

        namespace = dict(Optional=Optional, SpecifierSet=SpecifierSet,
                         InvalidSpecifier=InvalidSpecifier, Version=Version,
                         InvalidVersion=InvalidVersion)
        check = definition("core/plugin/plugin_registry.py", "_check_core_version",
                           namespace, "PluginManager")
        spec = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["core_version"]
        for version, allowed in (("v2.34.6", False), ("v2.34.7rc1", False),
                                 ("v2.34.7", True), ("v2.34.8", True),
                                 ("v2.35.0", True), ("v3.0.0", True)):
            with self.subTest(version=version):
                namespace["VERSION"] = version
                self.assertEqual(check(spec) is None, allowed)

    def test_actual_context_and_default_provider_failure_still_allow_fallback(self):
        class Client:
            model = types.SimpleNamespace(provider_id="backup", model_id="model")

        backup = Client()
        namespace = dict(Optional=Optional, LLMModelClient=Client)
        get_default = definition("core/provider/provider_manager.py", "get_default_llm",
                                 namespace, "ProviderManager")
        manager = types.SimpleNamespace(
            get_default_model_info=lambda _kind: None,
            get_model_client=lambda provider, model: backup if (provider, model) == ("backup", "model") else None,
        )
        manager.get_default_llm = types.MethodType(get_default, manager)
        context = types.SimpleNamespace(provider_mgr=manager)
        for name in ("get_llm_client", "get_default_llm_client"):
            method = definition("core/plugin/plugin_context.py", name, namespace, "PluginContext")
            setattr(context, name, types.MethodType(method, context))
        with self.assertRaises(AttributeError):
            context.get_default_llm_client()
        event = types.SimpleNamespace(model_group=[])
        warnings = []
        ModelRouter(context, {"primary_model": "default",
                              "fallback_models": ["backup:model"]}, warnings.append).route(event)
        self.assertEqual(event.model_group, [backup])
        self.assertEqual(len(warnings), 1)

    def test_actual_handler_registry_runs_low_priority_after_upstream(self):
        namespace = dict(Enum=Enum, IntEnum=IntEnum, dataclass=dataclass,
                         Optional=Optional, Union=Union, Callable=Callable,
                         Any=Any, Dict=Dict, List=List)
        for name in ("Priority", "EventType", "EventHandler", "EventHandlerRegistry"):
            definition("core/plugin/plugin_handlers.py", name, namespace)
        registry = namespace["EventHandlerRegistry"]()
        priorities = namespace["Priority"]
        event_type = namespace["EventType"].ON_IM_BATCH_MESSAGE
        event = types.SimpleNamespace(model_group=[])
        primary, backup = object(), object()
        context = types.SimpleNamespace(get_llm_client=lambda model_uuid: backup)
        router = ModelRouter(context, {"fallback_models": ["backup:model"]})

        async def upstream(e):
            e.model_group = [primary]

        async def route(e):
            router.route(e)

        registry.register(namespace["EventHandler"](event_type, priorities.LOW, route))
        registry.register(namespace["EventHandler"](event_type, priorities.MEDIUM, upstream))

        async def dispatch():
            for handler in registry.get_handlers(event_type):
                await handler.exec_handler(event)

        asyncio.run(dispatch())
        self.assertEqual(event.model_group, [primary, backup])


if __name__ == "__main__":
    unittest.main()
