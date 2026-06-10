import importlib.util
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SKILL_DIR / "scripts" / "decision.py"


def load_decision_module():
    spec = importlib.util.spec_from_file_location("decision", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_decision_module()

    def test_popularity_is_not_a_scoring_dimension(self):
        self.assertNotIn("popularity", self.module.DIMENSIONS)
        self.assertNotIn("热门度", self.module.DIMENSIONS.values())

    def test_hard_gate_rejects_private_or_sino_foreign_programs(self):
        profile = {
            "public_only": True,
            "accept_sino_foreign": False,
            "max_tuition": 10000,
            "province_only": True,
            "province": "江西",
        }
        private = {
            "school_type": "private",
            "sino_foreign": False,
            "tuition": 8000,
            "province": "江西",
        }
        expensive = {
            "school_type": "public",
            "sino_foreign": True,
            "tuition": 50000,
            "province": "江西",
        }
        self.assertFalse(self.module.hard_gate(profile, private)["eligible"])
        self.assertFalse(self.module.hard_gate(profile, expensive)["eligible"])

    def test_low_execution_fit_adds_structure_instead_of_stigma(self):
        route = self.module.execution_guidance("low")
        self.assertIn("外部约束", route)
        self.assertNotIn("烂泥", route)
        self.assertNotIn("没救", route)

    def test_missing_official_evidence_blocks_high_confidence(self):
        result = self.module.confidence_for_claim(
            [{"authority": "third_party", "freshness": "fresh"}],
            critical=True,
        )
        self.assertEqual(result, "insufficient")


if __name__ == "__main__":
    unittest.main()
