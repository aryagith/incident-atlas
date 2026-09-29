"""Export real ECS service metrics using an already configured AWS CLI profile."""

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cloud_metrics import NAME, WINDOW_SECONDS, timestamp


def queries(cluster, services):
    if not 2 <= len(services) <= 20 or len(set(services)) != len(services):
        raise ValueError("Choose 2–20 different ECS services")
    if any(not NAME.fullmatch(service) for service in services):
        raise ValueError("Service names must use letters, numbers, hyphens, or underscores")
    return [
        {
            "Id": f"m{index}", "Label": f"{service}.{label}",
            "MetricStat": {
                "Metric": {"Namespace": "AWS/ECS", "MetricName": metric, "Dimensions": [
                    {"Name": "ClusterName", "Value": cluster}, {"Name": "ServiceName", "Value": service},
                ]},
                "Period": 60, "Stat": "Average",
            },
        }
        for index, (service, label, metric) in enumerate(
            (service, label, metric)
            for service in services
            for label, metric in (("cpu", "CPUUtilization"), ("memory", "MemoryUtilization"))
        )
    ]


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True, help="ECS cluster name")
    parser.add_argument("--services", nargs="+", required=True, help="At least two ECS service names")
    parser.add_argument("--onset", required=True, help="Known incident onset, UTC ISO 8601 or Unix seconds")
    parser.add_argument("--region", required=True, help="AWS region containing the cluster")
    parser.add_argument("--profile", help="Configured AWS CLI profile; default profile if omitted")
    parser.add_argument("--output", type=Path, default=Path("cloudwatch-export.json"))
    args = parser.parse_args()
    try:
        onset = timestamp(args.onset)
        metric_queries = queries(args.cluster, args.services)
        if not shutil.which("aws"):
            raise ValueError("AWS CLI is not installed or not on PATH")
        command = ["aws", "cloudwatch", "get-metric-data", "--metric-data-queries", json.dumps(metric_queries),
                   "--start-time", iso(onset - WINDOW_SECONDS), "--end-time", iso(onset + WINDOW_SECONDS + 60),
                   "--scan-by", "TimestampAscending", "--region", args.region, "--output", "json"]
        if args.profile:
            command.extend(["--profile", args.profile])
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
        document = json.loads(result.stdout)
        if not document.get("MetricDataResults") or not any(item.get("Values") for item in document["MetricDataResults"]):
            raise ValueError("CloudWatch returned no points; check the region, cluster, services, and time window")
        if document.get("NextToken"):
            raise ValueError("CloudWatch export is incomplete; rerun without a page limit")
        document["IncidentAtlasUnits"] = {item["Label"]: "%" for item in metric_queries}
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, separators=(",", ":"))
        print(f"Saved {args.output}. Import it in Incident Atlas using the same onset: {iso(onset)}")
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        parser.exit(2, f"Export failed: {getattr(error, 'stderr', None) or error}\n")


if __name__ == "__main__":
    main()
