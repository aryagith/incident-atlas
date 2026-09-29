"""Checks a CloudWatch-shaped export at its real one-minute cadence."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from analyze import analyze
from aws_ecs_export import main, queries


BASE = 1700000000
ONSET = BASE + 1800


def sample():
    def result(label, values):
        return {"Id": label.replace(".", "_"), "Label": label, "StatusCode": "Complete",
                "Timestamps": [datetime.fromtimestamp(BASE + i * 60, timezone.utc).isoformat() for i in reversed(range(61))],
                "Values": list(reversed(values))}
    return json.dumps({"MetricDataResults": [
        result("checkout.cpu", [20 + (i % 3) for i in range(30)] + [90] * 31),
        result("checkout.memory", [40 + (i % 2) for i in range(30)] + [75] * 31),
        result("payments.cpu", [25 + (i % 3) for i in range(61)]),
        result("payments.memory", [45 + (i % 2) for i in range(61)]),
    ], "Messages": []})


class CloudImportCheck(unittest.TestCase):
    def test_cloudwatch_native_cadence_without_benchmark_model(self):
        document = json.loads(sample())
        document["IncidentAtlasUnits"] = {"checkout.cpu": "%"}
        result = analyze({"content": json.dumps(document), "onset": ONSET, "name": "ECS incident"})
        case = result["case"]
        self.assertEqual(case["kind"], "cloud")
        self.assertEqual(case["cadenceSeconds"], 60)
        self.assertEqual(case["services"][0]["id"], "checkout")
        self.assertEqual(case["services"][0]["evidenceRank"], 1)
        self.assertNotIn("modelRank", case["services"][0])
        self.assertEqual(case["services"][0]["firstAnomalySeconds"], 0)
        self.assertEqual(case["services"][0]["unit"], "%")

    def test_long_csv_and_duplicate_rejection(self):
        rows = ["timestamp,service,metric,value"]
        for i in range(61):
            rows.extend([f"{BASE + i * 60},checkout,cpu,{90 if i >= 30 else 20 + i % 3}",
                         f"{BASE + i * 60},payments,cpu,{25 + i % 3}"])
        csv = "\n".join(rows)
        case = analyze({"csv": csv, "onset": str(ONSET), "name": "CSV incident"})["case"]
        self.assertEqual(case["source"], "Long-form CSV")
        self.assertEqual(case["services"][0]["id"], "checkout")
        with self.assertRaisesRegex(ValueError, "Duplicate timestamp"):
            analyze({"csv": csv + "\n" + rows[1], "onset": ONSET, "name": "Duplicate"})

    def test_stopped_service_metrics_are_visible(self):
        document = json.loads(sample())
        for metric in document["MetricDataResults"][:2]:
            pairs = [(time, value) for time, value in zip(metric["Timestamps"], metric["Values"])
                     if datetime.fromisoformat(time).timestamp() < ONSET]
            metric["Timestamps"], metric["Values"] = map(list, zip(*pairs))
        case = analyze({"content": json.dumps(document), "onset": ONSET, "name": "Stopped service"})["case"]
        self.assertEqual(case["services"][0]["id"], "checkout")
        self.assertTrue(case["services"][0]["insufficientPostSamples"])
        self.assertEqual(case["services"][0]["status"], "missing")
        self.assertLess(case["services"][0]["times"][-1], case["onset"])
        self.assertGreater(case["time"][-1], case["onset"])

    def test_aws_query_contract(self):
        built = queries("production", ["checkout", "payments"])
        self.assertEqual([item["Label"] for item in built], ["checkout.cpu", "checkout.memory", "payments.cpu", "payments.memory"])
        self.assertEqual(built[0]["MetricStat"]["Metric"]["Dimensions"],
                         [{"Name": "ClusterName", "Value": "production"}, {"Name": "ServiceName", "Value": "checkout"}])
        self.assertTrue(all(item["MetricStat"]["Period"] == 60 for item in built))

    def test_exporter_writes_cli_response(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cloudwatch.json"
            arguments = ["aws_ecs_export.py", "--cluster", "production", "--services", "checkout", "payments",
                         "--onset", str(ONSET), "--region", "us-east-1", "--output", str(output)]
            with patch.object(sys, "argv", arguments), patch("aws_ecs_export.shutil.which", return_value="aws"), \
                 patch("aws_ecs_export.subprocess.run", return_value=SimpleNamespace(stdout=sample())) as run:
                main()
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(written["MetricDataResults"]), 4)
            self.assertEqual(written["IncidentAtlasUnits"]["checkout.cpu"], "%")
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ["aws", "cloudwatch", "get-metric-data"])
            self.assertIn("--scan-by", command)


if __name__ == "__main__":
    unittest.main()
