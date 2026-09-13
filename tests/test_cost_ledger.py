import tempfile
import unittest
from pathlib import Path

from app.cost_ledger import record, summarize_all_projects, summarize_project


class CostLedgerTests(unittest.TestCase):
    def test_empty_project_summarizes_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = summarize_project(Path(tmp))
            self.assertEqual(summary["totalUsd"], 0)
            self.assertEqual(summary["entryCount"], 0)
            self.assertFalse(summary["hasUnknownCostProvider"])

    def test_self_reported_cost_is_summed_into_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            record(pdir, provider="agent", kind="generate", usd=0.012, words=300)
            record(pdir, provider="agent", kind="regenerate", usd=0.003, words=40)
            summary = summarize_project(pdir)
            self.assertAlmostEqual(summary["totalUsd"], 0.015)
            self.assertAlmostEqual(summary["byProvider"]["agent"]["usd"], 0.015)
            self.assertEqual(summary["byProvider"]["agent"]["words"], 340)
            self.assertFalse(summary["hasUnknownCostProvider"])

    def test_provider_without_self_reported_cost_flags_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            record(pdir, provider="gemini", kind="generate", words=500)
            summary = summarize_project(pdir)
            self.assertEqual(summary["totalUsd"], 0)  # asla tahmin ÜRETİLMEZ
            self.assertTrue(summary["hasUnknownCostProvider"])
            self.assertTrue(summary["byProvider"]["gemini"]["hasUnknownCost"])
            self.assertEqual(summary["byProvider"]["gemini"]["words"], 500)

    def test_mixed_providers_only_known_costs_count_toward_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            record(pdir, provider="agent", kind="generate", usd=0.02, words=100)
            record(pdir, provider="openai", kind="generate", words=200)
            summary = summarize_project(pdir)
            self.assertAlmostEqual(summary["totalUsd"], 0.02)
            self.assertTrue(summary["hasUnknownCostProvider"])
            self.assertEqual(set(summary["byProvider"]), {"agent", "openai"})

    def test_entry_list_is_capped_to_bound_file_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            for _ in range(520):
                record(pdir, provider="agent", kind="generate", usd=0.001, words=10)
            summary = summarize_project(pdir)
            self.assertEqual(summary["entryCount"], 500)

    def test_summarize_all_projects_aggregates_across_project_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects_dir = Path(tmp)
            (projects_dir / "proje-a").mkdir()
            (projects_dir / "proje-b").mkdir()
            record(projects_dir / "proje-a", provider="agent", kind="generate", usd=0.01, words=50)
            record(projects_dir / "proje-b", provider="agent", kind="generate", usd=0.02, words=80)
            record(projects_dir / "proje-b", provider="gemini-vision", kind="vision_caption", requests=3)

            summary = summarize_all_projects(projects_dir)
            self.assertAlmostEqual(summary["totalUsd"], 0.03)
            self.assertEqual(summary["byProvider"]["gemini-vision"]["requests"], 3)

    def test_summarize_all_projects_handles_missing_directory_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = summarize_all_projects(Path(tmp) / "does-not-exist")
            self.assertEqual(summary["totalUsd"], 0)
            self.assertEqual(summary["entryCount"], 0)


if __name__ == "__main__":
    unittest.main()
