import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MetadataTests(unittest.TestCase):
    def test_manifest_and_schema_expose_the_v1_contract(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["plugin_id"], "kira-ai-plugin-model-router")
        self.assertEqual(
            manifest["repo"],
            "https://github.com/atsmoe/kira-ai-plugin-model-router",
        )
        self.assertEqual(
            set(schema),
            {
                "enabled",
                "primary_model",
                "fallback_models",
                "max_chain_length",
                "respect_existing_model_group",
            },
        )
        self.assertTrue(schema["enabled"]["default"])
        self.assertEqual(schema["primary_model"]["default"], "default")
        self.assertEqual(schema["fallback_models"]["default"], [])
        self.assertGreater(schema["max_chain_length"]["default"], 0)
        self.assertTrue(schema["respect_existing_model_group"]["default"])


if __name__ == "__main__":
    unittest.main()
