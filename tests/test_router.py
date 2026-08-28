import unittest
from types import SimpleNamespace

from router import ModelRouter


def client(provider_id: str, model_id: str):
    return SimpleNamespace(model=SimpleNamespace(provider_id=provider_id, model_id=model_id))


class FakeContext:
    def __init__(self, default=None, models=None):
        self.default = default
        self.models = models or {}

    def get_default_llm_client(self):
        return self.default

    def get_llm_client(self, model_uuid):
        return self.models.get(model_uuid)


class ModelRouterTests(unittest.TestCase):
    def test_disabled_router_leaves_event_unchanged(self):
        original = [client("provider-a", "upstream")]
        event = SimpleNamespace(model_group=original)
        context = FakeContext(default=client("provider-a", "default"))

        ModelRouter(context, {"enabled": False}).route(event)

        self.assertIs(event.model_group, original)

    def test_default_primary_uses_context_default_client(self):
        default = client("provider-a", "default")
        event = SimpleNamespace(model_group=[])

        ModelRouter(FakeContext(default=default), {"primary_model": "default"}).route(event)

        self.assertEqual(event.model_group, [default])

    def test_fixed_primary_resolves_by_model_uuid(self):
        fixed = client("provider-b", "model-1")
        event = SimpleNamespace(model_group=[])
        context = FakeContext(models={"provider-b:model-1": fixed})

        ModelRouter(context, {"primary_model": "provider-b:model-1"}).route(event)

        self.assertEqual(event.model_group, [fixed])

    def test_fallbacks_keep_same_and_cross_provider_order(self):
        primary = client("provider-a", "main")
        same_provider = client("provider-a", "backup")
        cross_provider = client("provider-b", "backup")
        context = FakeContext(models={
            "provider-a:main": primary,
            "provider-a:backup": same_provider,
            "provider-b:backup": cross_provider,
        })
        event = SimpleNamespace(model_group=[])

        ModelRouter(context, {
            "primary_model": "provider-a:main",
            "fallback_models": ["provider-a:backup", "provider-b:backup"],
        }).route(event)

        self.assertEqual(event.model_group, [primary, same_provider, cross_provider])

    def test_invalid_and_unavailable_references_are_ignored_safely(self):
        primary = client("provider-a", "main")
        warnings = []
        event = SimpleNamespace(model_group=[])
        context = FakeContext(models={"provider-a:main": primary})

        ModelRouter(context, {
            "primary_model": "provider-a:main",
            "fallback_models": ["SECRET_WITHOUT_SEPARATOR", "provider-z:missing"],
        }, warnings.append).route(event)

        self.assertEqual(event.model_group, [primary])
        self.assertEqual(len(warnings), 2)
        self.assertNotIn("SECRET_WITHOUT_SEPARATOR", " ".join(warnings))
        self.assertNotIn("provider-z:missing", " ".join(warnings))

    def test_duplicates_are_removed_without_reordering(self):
        primary = client("provider-a", "main")
        duplicate_instance = client("provider-a", "main")
        backup = client("provider-b", "backup")
        event = SimpleNamespace(model_group=[])
        context = FakeContext(models={
            "provider-a:main": primary,
            "provider-a:duplicate": duplicate_instance,
            "provider-b:backup": backup,
        })

        ModelRouter(context, {
            "primary_model": "provider-a:main",
            "fallback_models": ["provider-a:duplicate", "provider-b:backup", "provider-b:backup"],
        }).route(event)

        self.assertEqual(event.model_group, [primary, backup])

    def test_existing_model_group_is_preserved_and_fallbacks_are_appended(self):
        upstream_primary = client("provider-upstream", "main")
        upstream_backup = client("provider-upstream", "backup")
        configured_primary = client("provider-a", "main")
        appended = client("provider-b", "backup")
        event = SimpleNamespace(model_group=[upstream_primary, upstream_backup])
        context = FakeContext(models={
            "provider-a:main": configured_primary,
            "provider-b:backup": appended,
        })

        ModelRouter(context, {
            "primary_model": "provider-a:main",
            "fallback_models": ["provider-b:backup"],
            "respect_existing_model_group": True,
        }).route(event)

        self.assertEqual(event.model_group, [upstream_primary, upstream_backup, appended])

    def test_existing_model_group_can_be_replaced_explicitly(self):
        upstream = client("provider-upstream", "main")
        configured = client("provider-a", "main")
        event = SimpleNamespace(model_group=[upstream])
        context = FakeContext(models={"provider-a:main": configured})

        ModelRouter(context, {
            "primary_model": "provider-a:main",
            "respect_existing_model_group": False,
        }).route(event)

        self.assertEqual(event.model_group, [configured])

    def test_empty_or_wholly_invalid_configuration_leaves_event_unchanged(self):
        default = client("provider-a", "default")
        context = FakeContext(default=default)

        for config in ({}, {
            "primary_model": "invalid",
            "fallback_models": ["provider-z:missing"],
        }):
            with self.subTest(config=config):
                original = []
                event = SimpleNamespace(model_group=original)
                ModelRouter(context, config).route(event)
                self.assertIs(event.model_group, original)

    def test_max_chain_length_caps_plugin_built_chain(self):
        primary = client("provider-a", "main")
        backup_1 = client("provider-b", "one")
        backup_2 = client("provider-c", "two")
        context = FakeContext(models={
            "provider-a:main": primary,
            "provider-b:one": backup_1,
            "provider-c:two": backup_2,
        })
        event = SimpleNamespace(model_group=[])

        ModelRouter(context, {
            "primary_model": "provider-a:main",
            "fallback_models": ["provider-b:one", "provider-c:two"],
            "max_chain_length": 2,
        }).route(event)

        self.assertEqual(event.model_group, [primary, backup_1])


if __name__ == "__main__":
    unittest.main()
