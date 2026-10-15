"""Stable-ID routing across KiraAI v3 model catalogs; no provider calls."""

import unittest
from types import SimpleNamespace as NS

from router import ModelRouter


def model(identity, name, kind="llm"):
    return NS(provider_id="p", model_id=identity, model_name=name,
              model_type=NS(value=kind))


class ModelIdentityTests(unittest.TestCase):
    def context(self, models, aliases=None, disabled=False):
        aliases = aliases or {}
        clients = {m.model_id: NS(model=m) for m in models}
        calls = []

        def lookup(provider_id, token, kind="llm"):
            identity = aliases.get(token, token)
            return next((m for m in models if provider_id == "p" and
                         m.model_id == identity and m.model_type.value == kind), None)

        def get_client(model_uuid):
            calls.append(model_uuid)
            provider_id, token = model_uuid.split(":", 1)
            found = lookup(provider_id, token)
            return clients[found.model_id] if found and not disabled else None

        context = NS(get_default_llm_client=lambda: None, get_llm_client=get_client,
                     provider_mgr=NS(
                         get_all_providers=lambda: {"p": NS(provider_id="p", provider_name="Friendly")},
                         get_model_infos=lambda provider_id: models if provider_id == "p" else [],
                         get_model_info=lookup))
        return context, clients, calls

    def route(self, context, references):
        event, warnings = NS(model_group=[]), []
        ModelRouter(context, {"fallback_models": references}, warnings.append).route(event)
        return event.model_group, warnings

    def test_v3_upstream_names_resolve_to_stable_ids_and_deduplicate(self):
        context, clients, _ = self.context([model("stable-id", "upstream:variant")])
        for references in (["p:upstream:variant"], ["Friendly：upstream:variant"],
                           ["p:upstream:variant", "Friendly：upstream:variant", "p:stable-id"]):
            with self.subTest(references=references):
                chain, _ = self.route(context, references)
                self.assertEqual(chain, [clients["stable-id"]])

    def test_legacy_alias_wins_over_another_models_upstream_name(self):
        context, clients, _ = self.context(
            [model("old-id", "renamed"), model("other-id", "old-name")],
            aliases={"old-name": "old-id"})
        chain, _ = self.route(context, ["p:old-name"])
        self.assertEqual(chain, [clients["old-id"]])

    def test_ambiguous_model_names_fail_closed(self):
        context, _, _ = self.context([model("a", "same"), model("b", "same")])
        chain, warnings = self.route(context, ["p:same"])
        self.assertEqual(chain, [])
        self.assertIn("ambiguous", " ".join(warnings))

    def test_only_llm_names_match(self):
        context, clients, _ = self.context([model("llm-id", "shared"), model("embed-id", "shared", "embedding")])
        chain, _ = self.route(context, ["p:shared"])
        self.assertEqual(chain, [clients["llm-id"]])

    def test_disabled_resolved_identity_is_not_reinterpreted_as_name(self):
        context, _, calls = self.context(
            [model("original", "renamed"), model("other", "original")], disabled=True)
        chain, _ = self.route(context, ["p:original"])
        self.assertEqual(chain, [])
        self.assertNotIn("p:other", calls)

    def test_model_catalog_failure_is_safe_and_does_not_leak_details(self):
        context, _, _ = self.context([])
        context.provider_mgr.get_model_infos = lambda _: (_ for _ in ()).throw(RuntimeError("PRIVATE"))
        chain, warnings = self.route(context, ["Friendly:missing"])
        self.assertEqual(chain, [])
        self.assertTrue(warnings)
        self.assertNotIn("PRIVATE", " ".join(warnings))

    def test_fixed_client_exception_still_allows_later_fallback(self):
        context, clients, _ = self.context([model("ok", "working")])
        get_client = context.get_llm_client

        def resolve(model_uuid):
            if model_uuid == "p:bad":
                raise RuntimeError("PRIVATE")
            return get_client(model_uuid)

        context.get_llm_client = resolve
        chain, warnings = self.route(context, ["p:bad", "p:ok"])
        self.assertEqual(chain, [clients["ok"]])
        self.assertNotIn("PRIVATE", " ".join(warnings))


if __name__ == "__main__":
    unittest.main()
