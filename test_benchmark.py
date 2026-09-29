"""Small checks against downloaded RCAEval cases; run after benchmark.py fetch."""

import json
import unittest
from pathlib import Path

from benchmark import load_baro, rank_baro, rank_fusion, read_case, selected_cases, severity


class BenchmarkCheck(unittest.TestCase):
    def test_real_case_and_selection(self):
        rows = selected_cases()
        self.assertEqual(len(rows), 75)
        self.assertEqual(len({(row["system"], row["root_cause_service"], row["fault"]) for row in rows}), 75)
        self.assertIn("re1ob_currencyservice_loss_2", {row["case"] for row in rows})
        self.assertNotIn("re1ob_currencyservice_loss_1", {row["case"] for row in rows})
        repeats = selected_cases({row["case"] for row in rows})
        self.assertEqual(len(repeats), 75)
        self.assertFalse({row["case"] for row in rows} & {row["case"] for row in repeats})
        self.assertIn("re1ob_currencyservice_loss_3", {row["case"] for row in repeats})
        used = {row["case"] for row in rows + repeats}
        for _ in range(2):
            cohort = selected_cases(used)
            used.update(row["case"] for row in cohort)
        self.assertEqual(len(selected_cases(used, expected=73)), 73)
        case = read_case(next(row for row in rows if row["case"] == "re1ob_adservice_cpu_1"))
        self.assertEqual(case["time"][case["onsetIndex"]], case["onset"])
        self.assertEqual(case["groundTruth"], "adservice")
        self.assertIn("adservice", {service["id"] for service in case["services"]})
        self.assertTrue(any(service["firstAnomalySeconds"] is not None for service in case["services"]))
        case["rankTimeMs"] = {}
        case["truthRanks"] = {}
        rank_baro(case, load_baro())
        self.assertEqual(case["truthRanks"]["baro"], 1)

    def test_severity_boundaries(self):
        self.assertEqual([severity(value) for value in (2.9, 3, 5.9, 6)], ["normal", "warning", "warning", "critical"])

    def test_fusion_uses_both_ranks(self):
        case = {"groundTruth": "b", "truthRanks": {}, "rankTimeMs": {}, "services": [
            {"id": "a", "modelRank": 1, "baroRank": 4},
            {"id": "b", "modelRank": 2, "baroRank": 1},
        ]}
        rank_fusion(case)
        self.assertEqual(case["truthRanks"]["fusion"], 1)

    def test_saved_cohorts_are_disjoint(self):
        web = Path(__file__).parent / "web"
        primary, second, validation = [json.loads((web / name).read_text(encoding="utf-8")) for name in ("benchmark.json", "sensitivity.json", "validation.json")]
        ids = [{case["id"] for case in bundle["cases"]} for bundle in (primary, second, validation)]
        self.assertEqual([len(group) for group in ids], [75, 75, 223])
        self.assertFalse(ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
        self.assertEqual(set(validation["trainingCaseIds"]), ids[0])
        self.assertEqual(set(validation["developmentCaseIds"]), ids[1])
        self.assertEqual([len(validation["cohorts"][name]["caseIds"]) for name in ("third", "fourth", "last")], [75, 75, 73])

    def test_validation_cases_match_saved_evaluation(self):
        web = Path(__file__).parent / "web"
        summary = json.loads((web / "validation.json").read_text(encoding="utf-8"))
        details = json.loads((web / "validation-cases.json").read_text(encoding="utf-8"))
        indexed = {case["id"]: case for case in summary["cases"]}
        self.assertEqual(details["kind"], "validation-cases")
        self.assertEqual(len(details["cases"]), 223)
        self.assertEqual({case["id"] for case in details["cases"]}, indexed.keys())
        for case in details["cases"]:
            saved = indexed[case["id"]]
            self.assertEqual(case["groundTruth"], saved["rootService"])
            self.assertEqual(case["truthRanks"], saved["truthRanks"])
            self.assertTrue(case["services"] and case["time"])


if __name__ == "__main__":
    unittest.main()
