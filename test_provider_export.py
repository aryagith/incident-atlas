"""Provider API mapping checks using documented Azure and GCP response shapes."""

import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from analyze import analyze
from provider_export import export_gcp, main, map_azure, map_gcp


BASE = 1700000000
ONSET = BASE + 1800


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def azure_response(service):
    values = []
    for name, field, normal, changed in (("UsageNanoCores", "average", 20, 90),
                                         ("WorkingSetBytes", "average", 40, 40),
                                         ("Requests", "total", 100, 100)):
        rows = [{"timeStamp": iso(BASE + i * 60), field: changed if service == "checkout" and name == "UsageNanoCores" and i >= 30 else normal}
                for i in range(61)]
        values.append({"name": {"value": name}, "unit": "Count", "timeseries": [{"data": rows}]})
    return {"value": values}


def gcp_response(service, metric):
    def value(i):
        if metric.endswith("utilizations"):
            return {"doubleValue": 0.9 if service == "checkout" and metric.startswith("container/cpu") and i >= 30 else 0.2}
        return {"int64Value": "100"}
    return {"timeSeries": [{"points": [{"interval": {"endTime": iso(BASE + i * 60)}, "value": value(i)}
                                       for i in reversed(range(61))]}]}


class ProviderExportCheck(unittest.TestCase):
    def test_azure_response_maps_average_and_total(self):
        mapped = map_azure(azure_response("checkout"), "checkout")
        self.assertEqual([(item["metric"], item["unit"]) for item in mapped],
                         [("cpu", "NanoCores"), ("memory", "Bytes"), ("requests", "Count/min")])
        with self.assertRaisesRegex(ValueError, "multiple dimension series"):
            document = azure_response("checkout")
            document["value"][0]["timeseries"].append({"data": []})
            map_azure(document, "checkout")

    def test_azure_cli_export_imports_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "azure.json"
            argv = ["provider_export.py", "azure", "--subscription", "11111111-1111-1111-1111-111111111111",
                    "--resource-group", "prod", "--services", "checkout", "payments", "--onset", str(ONSET),
                    "--output", str(output)]
            responses = [SimpleNamespace(stdout=json.dumps(azure_response(service))) for service in ("checkout", "payments")]
            with patch.object(sys, "argv", argv), patch("provider_export.shutil.which", return_value="az"), \
                 patch("provider_export.subprocess.run", side_effect=responses) as run:
                main()
            content = output.read_text(encoding="utf-8")
            case = analyze({"content": content, "onset": ONSET, "name": "Azure incident"})["case"]
            self.assertEqual(case["source"], "Azure Container Apps")
            self.assertEqual(case["services"][0]["name"], "checkout")
            self.assertIn("Microsoft.App%2Fcontainerapps", run.call_args_list[0].args[0][5])
            self.assertIn("aggregation=Average%2CTotal", run.call_args_list[0].args[0][5])

    def test_gcp_monitoring_mapping_and_import(self):
        requests = []

        def fetch(request, timeout):
            url = urlparse(request.full_url)
            params = parse_qs(url.query)
            query = params["filter"][0]
            service = "checkout" if 'service_name="checkout"' in query else "payments"
            metric = next(metric for metric in ("container/cpu/utilizations", "container/memory/utilizations", "request_count")
                          if f"run.googleapis.com/{metric}" in query)
            requests.append((params, request.get_header("Authorization")))
            return io.BytesIO(json.dumps(gcp_response(service, metric)).encode("utf-8"))

        args = SimpleNamespace(project="my-project", region="us-central1", services=["checkout", "payments"])
        with patch("provider_export.shutil.which", return_value="gcloud"), \
             patch("provider_export.subprocess.run", return_value=SimpleNamespace(stdout="secret-token\n")), \
             patch("provider_export.urlopen", side_effect=fetch):
            provider, mapped = export_gcp(args, ONSET)
        case = analyze({"content": json.dumps({"IncidentAtlasProvider": provider, "IncidentAtlasSeries": mapped}),
                        "onset": ONSET, "name": "GCP incident"})["case"]
        self.assertEqual(case["source"], "GCP Cloud Run")
        self.assertEqual(case["services"][0]["name"], "checkout")
        self.assertEqual(mapped[0]["unit"], "%")
        self.assertEqual(mapped[0]["points"][0][1], 90)
        self.assertEqual(len(requests), 6)
        self.assertEqual(requests[0][0]["aggregation.perSeriesAligner"], ["ALIGN_PERCENTILE_50"])
        self.assertEqual(requests[2][0]["aggregation.crossSeriesReducer"], ["REDUCE_SUM"])
        self.assertEqual(requests[0][1], "Bearer secret-token")

    def test_gcp_pagination_merges_points_from_one_reduced_series(self):
        response = gcp_response("checkout", "request_count")
        first = {"timeSeries": [{"points": response["timeSeries"][0]["points"][:30]}], "nextPageToken": "next"}
        second = {"timeSeries": [{"points": response["timeSeries"][0]["points"][30:]}]}
        mapped = map_gcp([first, second], "checkout", "request_count")
        self.assertEqual(len(mapped["points"]), 61)
        with self.assertRaisesRegex(ValueError, "duplicate points"):
            map_gcp([first, first], "checkout", "request_count")

    def test_provider_export_rejects_malformed_points(self):
        with self.assertRaisesRegex(ValueError, "pairs"):
            analyze({"content": json.dumps({"IncidentAtlasProvider": "GCP Cloud Run", "IncidentAtlasSeries": [
                {"service": "checkout", "metric": "cpu", "unit": "%", "points": [[ONSET]]}]}),
                "onset": ONSET, "name": "Malformed"})


if __name__ == "__main__":
    unittest.main()
