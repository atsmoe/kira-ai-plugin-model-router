import asyncio
import importlib.util
import inspect
import sys
import types
import unittest
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


class Priority(IntEnum):
    """Current KiraAI user-visible handler priorities."""

    SYS_LOW = -100
    LOW = -50
    MEDIUM = 0
    HIGH = 50
    SYS_HIGH = 100


class EventType(Enum):
    ON_IM_BATCH_MESSAGE = "on_im_batch_message"


@dataclass
class EventHandler:
    event_type: EventType
    priority: int
    handler: object

    def __lt__(self, other):
        return self.priority < other.priority


class EventHandlerRegistry:
    """Match KiraAI's descending EventHandlerRegistry ordering."""

    def __init__(self):
        self.handlers = []

    def register(self, handler):
        self.handlers.append(handler)
        self.handlers.sort(reverse=True)

    async def execute(self, event):
        for handler in self.handlers:
            await handler.handler(event)


class BasePlugin(ABC):
    """Faithful minimum of current KiraAI's abstract lifecycle contract."""

    def __init__(self, ctx, cfg):
        self.ctx = ctx
        self.plugin_cfg = cfg

    @abstractmethod
    async def initialize(self):
        pass

    @abstractmethod
    async def terminate(self):
        pass


class On:
    def __init__(self, hooks):
        self.hooks = hooks

    def im_batch_message(self, priority=Priority.MEDIUM):
        def decorator(func):
            self.hooks.append(
                EventHandler(EventType.ON_IM_BATCH_MESSAGE, priority, func)
            )
            return func

        return decorator


def client(provider_id: str, model_id: str):
    return SimpleNamespace(
        model=SimpleNamespace(provider_id=provider_id, model_id=model_id)
    )


class FakeContext:
    def __init__(self, default=None, models=None):
        self.default = default
        self.models = models or {}

    def get_default_llm_client(self):
        return self.default

    def get_llm_client(self, model_uuid):
        return self.models.get(model_uuid)


def load_plugin_module():
    hooks = []
    package = types.ModuleType("model_router_plugin")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package

    core = types.ModuleType("core")
    core_chat = types.ModuleType("core.chat")
    core_plugin = types.ModuleType("core.plugin")
    core_chat.KiraMessageBatchEvent = object
    core_plugin.BasePlugin = BasePlugin
    core_plugin.Priority = Priority
    core_plugin.logger = SimpleNamespace(warning=lambda _message: None)
    core_plugin.on = On(hooks)
    sys.modules["core"] = core
    sys.modules["core.chat"] = core_chat
    sys.modules["core.plugin"] = core_plugin

    spec = importlib.util.spec_from_file_location(
        "model_router_plugin.main", ROOT / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, hooks


def bind_plugin_hook(plugin, hooks):
    hook = hooks[0]
    return EventHandler(
        hook.event_type,
        hook.priority,
        getattr(plugin, hook.handler.__name__),
    )


class PluginEntrypointTests(unittest.TestCase):
    def test_loader_contract_instantiates_initializes_and_registers_batch_hook(self):
        module, hooks = load_plugin_module()
        self.assertFalse(inspect.isabstract(module.ModelRouterPlugin))

        plugin = module.ModelRouterPlugin(FakeContext(), {})
        asyncio.run(plugin.initialize())

        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0].event_type, EventType.ON_IM_BATCH_MESSAGE)
        self.assertEqual(hooks[0].priority, Priority.LOW)
        self.assertTrue(callable(bind_plugin_hook(plugin, hooks).handler))
        asyncio.run(plugin.terminate())

    def test_low_priority_composes_after_upstream_and_preserves_append_dedup_cap(self):
        module, hooks = load_plugin_module()
        upstream_primary = client("upstream", "primary")
        upstream_backup = client("upstream", "backup")
        duplicate_backup = client("upstream", "backup")
        cross_backup_1 = client("provider-b", "backup-1")
        cross_backup_2 = client("provider-c", "backup-2")
        context = FakeContext(models={
            "upstream:duplicate": duplicate_backup,
            "provider-b:backup-1": cross_backup_1,
            "provider-c:backup-2": cross_backup_2,
        })
        plugin = module.ModelRouterPlugin(context, {
            "primary_model": "default",
            "fallback_models": [
                "upstream:duplicate",
                "provider-b:backup-1",
                "provider-c:backup-2",
            ],
            "max_chain_length": 3,
            "respect_existing_model_group": True,
        })
        asyncio.run(plugin.initialize())
        event = SimpleNamespace(model_group=[])

        async def upstream_router(batch_event):
            batch_event.model_group = [upstream_primary, upstream_backup]

        registry = EventHandlerRegistry()
        registry.register(
            EventHandler(
                EventType.ON_IM_BATCH_MESSAGE,
                Priority.MEDIUM,
                upstream_router,
            )
        )
        registry.register(bind_plugin_hook(plugin, hooks))
        asyncio.run(registry.execute(event))

        self.assertEqual(
            event.model_group,
            [upstream_primary, upstream_backup, cross_backup_1],
        )

    def test_low_priority_explicit_replace_controls_final_event(self):
        module, hooks = load_plugin_module()
        upstream = client("upstream", "primary")
        configured_primary = client("provider-a", "primary")
        configured_backup = client("provider-b", "backup")
        context = FakeContext(models={
            "provider-a:primary": configured_primary,
            "provider-b:backup": configured_backup,
        })
        plugin = module.ModelRouterPlugin(context, {
            "primary_model": "provider-a:primary",
            "fallback_models": ["provider-b:backup"],
            "max_chain_length": 2,
            "respect_existing_model_group": False,
        })
        asyncio.run(plugin.initialize())
        event = SimpleNamespace(model_group=[])

        async def upstream_router(batch_event):
            batch_event.model_group = [upstream]

        registry = EventHandlerRegistry()
        registry.register(
            EventHandler(
                EventType.ON_IM_BATCH_MESSAGE,
                Priority.MEDIUM,
                upstream_router,
            )
        )
        registry.register(bind_plugin_hook(plugin, hooks))
        asyncio.run(registry.execute(event))

        self.assertEqual(event.model_group, [configured_primary, configured_backup])


if __name__ == "__main__":
    unittest.main()
