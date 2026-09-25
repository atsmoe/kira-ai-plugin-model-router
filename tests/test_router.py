import unittest
from types import SimpleNamespace

from router import ModelRouter


def client(provider_id: str, model_id: str):
    return SimpleNamespace(model=SimpleNamespace(provider_id=provider_id, model_id=model_id))


def provider(provider_id: str, provider_name: str):
    return SimpleNamespace(provider_id=provider_id, provider_name=provider_name)


class FakeContext:
    def __init__(self, default=None, models=None, providers=None):
        self.default = default
        self.models = models or {}
        if providers is not None:
            self.provider_mgr = SimpleNamespace(get_all_providers=lambda: providers)

    def get_default_llm_client(self):
        return self.default

    def get_llm_client(self, model_uuid):
        return self.models.get(model_uuid)


class ModelRouterTests(unittest.TestCase):
    def test_default_lookup_error_still_routes_to_valid_fallback(self):
        backup = client("provider-b", "backup")
        context = FakeContext(models={"provider-b:backup": backup})
        context.get_default_llm_client = lambda: (_ for _ in ()).throw(
            ValueError("private provider details")
        )
        warnings = []
        event = SimpleNamespace(model_group=[])
        ModelRouter(context, {
            "primary_model": "default", "fallback_models": ["provider-b:backup"],
        }, warnings.append).route(event)
        self.assertEqual(event.model_group, [backup])
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("private provider details", warnings[0])

    def test_default_lookup_error_without_fallback_keeps_original_group(self):
        context = FakeContext()
        context.get_default_llm_client = lambda: (_ for _ in ()).throw(TypeError("private"))
        original = [client("upstream", "main")]
        event = SimpleNamespace(model_group=original)
        ModelRouter(context, {
            "primary_model": "default", "respect_existing_model_group": False,
        }).route(event)
        self.assertIs(event.model_group, original)

    def test_invalid_fallback_container_keeps_primary_without_interpreting_keys(self):
        primary = client("provider-a", "main")
        backup = client("provider-b", "backup")
        for value in (None, 42, True, "provider-b:backup", {"provider-b:backup": True}):
            with self.subTest(value=value):
                warnings = []
                event = SimpleNamespace(model_group=[])
                context = FakeContext(default=primary, models={"provider-b:backup": backup})
                ModelRouter(context, {
                    "primary_model": "default", "fallback_models": value,
                }, warnings.append).route(event)
                self.assertEqual(event.model_group, [primary])
                self.assertEqual(len(warnings), 1)
                self.assertNotIn("provider-b", warnings[0])

    def test_invalid_chain_limits_use_default_without_crashing_or_truncating(self):
        primary = client("provider-a", "main")
        backup = client("provider-b", "backup")
        for value in (True, False, 1.5, float("inf"), float("nan"), None, 0, -1, "bad"):
            with self.subTest(value=value):
                warnings = []
                event = SimpleNamespace(model_group=[])
                context = FakeContext(default=primary, models={"provider-b:backup": backup})
                ModelRouter(context, {
                    "primary_model": "default",
                    "fallback_models": ["provider-b:backup"],
                    "max_chain_length": value,
                }, warnings.append).route(event)
                self.assertEqual(event.model_group, [primary, backup])
                self.assertEqual(len(warnings), 1)

    def test_integer_string_limit_remains_supported(self):
        primary = client("provider-a", "main")
        backup = client("provider-b", "backup")
        event = SimpleNamespace(model_group=[])
        ModelRouter(FakeContext(default=primary, models={"provider-b:backup": backup}), {
            "primary_model": "default", "fallback_models": ["provider-b:backup"],
            "max_chain_length": "1",
        }).route(event)
        self.assertEqual(event.model_group, [primary])

    def test_existing_group_over_limit_is_not_truncated(self):
        original = [client("upstream", str(i)) for i in range(3)]
        event = SimpleNamespace(model_group=original)
        backup = client("provider-b", "backup")
        ModelRouter(FakeContext(models={"provider-b:backup": backup}), {
            "fallback_models": ["provider-b:backup"], "max_chain_length": 1,
        }).route(event)
        self.assertEqual(event.model_group, original)

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

    def test_display_name_with_full_width_colon_resolves_exact_provider(self):
        fallback = client("provider-b", "model-1")
        event = SimpleNamespace(model_group=[])
        context = FakeContext(
            models={"provider-b:model-1": fallback},
            providers={"provider-b": provider("provider-b", "Friendly Provider")},
        )

        ModelRouter(context, {
            "fallback_models": ["Friendly Provider：model-1"],
        }).route(event)

        self.assertEqual(event.model_group, [fallback])

    def test_display_name_with_ascii_colon_trims_surrounding_whitespace(self):
        fallback = client("provider-b", "model-1")
        event = SimpleNamespace(model_group=[])
        context = FakeContext(
            models={"provider-b:model-1": fallback},
            providers={"provider-b": provider("provider-b", "Friendly Provider")},
        )

        ModelRouter(context, {
            "fallback_models": ["  Friendly Provider : model-1  "],
        }).route(event)

        self.assertEqual(event.model_group, [fallback])

    def test_display_name_can_select_primary_model(self):
        primary = client("provider-b", "model-1")
        event = SimpleNamespace(model_group=[])
        context = FakeContext(
            models={"provider-b:model-1": primary},
            providers={"provider-b": provider("provider-b", "Friendly Provider")},
        )

        ModelRouter(context, {
            "primary_model": "Friendly Provider:model-1",
        }).route(event)

        self.assertEqual(event.model_group, [primary])

    def test_model_id_after_first_separator_is_preserved(self):
        fallback = client("provider-b", "model:variant")
        event = SimpleNamespace(model_group=[])
        context = FakeContext(
            models={"provider-b:model:variant": fallback},
            providers={"provider-b": provider("provider-b", "Friendly Provider")},
        )

        ModelRouter(context, {
            "fallback_models": ["Friendly Provider:model:variant"],
        }).route(event)

        self.assertEqual(event.model_group, [fallback])

    def test_direct_provider_id_resolution_precedes_display_name_lookup(self):
        direct = client("alias-id", "model-1")
        alias_target = client("provider-b", "model-1")
        calls = []
        context = FakeContext(models={
            "alias-id:model-1": direct,
            "provider-b:model-1": alias_target,
        })
        context.provider_mgr = SimpleNamespace(
            get_all_providers=lambda: calls.append("called") or {
                "provider-b": provider("provider-b", "alias-id"),
            }
        )
        event = SimpleNamespace(model_group=[])

        ModelRouter(context, {
            "fallback_models": ["alias-id:model-1"],
        }).route(event)

        self.assertEqual(event.model_group, [direct])
        self.assertEqual(calls, [])

    def test_existing_provider_id_with_missing_model_is_not_reinterpreted_as_name(self):
        upstream = client("upstream", "main")
        wrong_provider_model = client("other-id", "model-1")
        warnings = []
        event = SimpleNamespace(model_group=[upstream])
        context = FakeContext(
            models={"other-id:model-1": wrong_provider_model},
            providers={
                "stable-id": provider("stable-id", "Stable Provider"),
                "other-id": provider("other-id", "stable-id"),
            },
        )

        ModelRouter(context, {
            "fallback_models": ["stable-id:model-1"],
        }, warnings.append).route(event)

        self.assertEqual(event.model_group, [upstream])
        self.assertNotIn(wrong_provider_model, event.model_group)
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("stable-id", warnings[0])
        self.assertNotIn("model-1", warnings[0])

    def test_unknown_display_name_fails_closed_without_echoing_reference(self):
        warnings = []
        upstream = client("upstream", "main")
        event = SimpleNamespace(model_group=[upstream])
        context = FakeContext(models={}, providers={})

        ModelRouter(context, {
            "fallback_models": ["PRIVATE PROVIDER:secret-model"],
        }, warnings.append).route(event)

        self.assertEqual(event.model_group, [upstream])
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("PRIVATE PROVIDER", warnings[0])
        self.assertNotIn("secret-model", warnings[0])

    def test_duplicate_display_names_fail_closed(self):
        first = client("provider-a", "model-1")
        second = client("provider-b", "model-1")
        warnings = []
        upstream = client("upstream", "main")
        event = SimpleNamespace(model_group=[upstream])
        context = FakeContext(
            models={
                "provider-a:model-1": first,
                "provider-b:model-1": second,
            },
            providers={
                "provider-a": provider("provider-a", "Duplicated Name"),
                "provider-b": provider("provider-b", "Duplicated Name"),
            },
        )

        ModelRouter(context, {
            "fallback_models": ["Duplicated Name:model-1"],
        }, warnings.append).route(event)

        self.assertEqual(event.model_group, [upstream])
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("Duplicated Name", warnings[0])

    def test_missing_or_incompatible_provider_manager_fails_safely(self):
        contexts = [
            FakeContext(models={}),
            FakeContext(models={}, providers=[]),
            FakeContext(models={}, providers={}),
        ]
        contexts[2].provider_mgr = SimpleNamespace(
            get_all_providers=lambda: (_ for _ in ()).throw(RuntimeError("private detail"))
        )

        for context in contexts:
            with self.subTest(provider_mgr=getattr(context, "provider_mgr", None)):
                warnings = []
                upstream = client("upstream", "main")
                event = SimpleNamespace(model_group=[upstream])
                ModelRouter(context, {
                    "fallback_models": ["PRIVATE PROVIDER:secret-model"],
                }, warnings.append).route(event)

                self.assertEqual(event.model_group, [upstream])
                self.assertEqual(len(warnings), 1)
                self.assertNotIn("PRIVATE PROVIDER", warnings[0])
                self.assertNotIn("secret-model", warnings[0])
                self.assertNotIn("private detail", warnings[0])

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
