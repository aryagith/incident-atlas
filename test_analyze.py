"""Checks the shipped CSV through the same analysis path as the local API."""

import unittest
from pathlib import Path

from analyze import analyze, parse_csv


SAMPLE = (Path(__file__).parent / "web" / "example.csv").read_text(encoding="utf-8")
ONSET = 1685202688


class ImportCheck(unittest.TestCase):
    def test_sample_ranks_unlabelled_services(self):
        result = analyze({"csv": SAMPLE, "onset": ONSET, "name": "Sample incident"})
        case = result["case"]
        self.assertEqual(result["modelTrainingCases"], 373)
        self.assertEqual(case["kind"], "uploaded")
        self.assertIsNone(case["groundTruth"])
        self.assertEqual(case["truthRanks"], {})
        self.assertEqual(len({service["fusionRank"] for service in case["services"]}), len(case["services"]))
        self.assertEqual(min(case["services"], key=lambda service: service["fusionRank"])["name"], "adservice")

    def test_csv_rejects_gaps_and_invalid_numbers(self):
        lines = SAMPLE.splitlines()
        gap = lines.copy()
        gap[2] = gap[2].replace(gap[2].split(",", 1)[0], str(int(gap[1].split(",", 1)[0]) + 2), 1)
        with self.assertRaisesRegex(ValueError, "consecutive"):
            parse_csv("\n".join(gap), ONSET, "Gap")
        bad = lines.copy()
        cells = bad[1].split(",")
        cells[1] = "infinity"
        bad[1] = ",".join(cells)
        with self.assertRaisesRegex(ValueError, "finite"):
            parse_csv("\n".join(bad), ONSET, "Bad value")

    def test_timestamp_bounds(self):
        with self.assertRaisesRegex(ValueError, "Unix timestamp"):
            parse_csv(SAMPLE, 999999999999999999999, "Too late")


if __name__ == "__main__":
    unittest.main()
