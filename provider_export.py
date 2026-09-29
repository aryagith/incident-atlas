"""Export Azure Container Apps or GCP Cloud Run metrics for Incident Atlas."""

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cloud_metrics import NAME, WINDOW_SECONDS, analyze_cloud, numeric, timestamp


AZURE_METRICS = {"UsageNanoCores": ("cpu", "NanoCores", "average"),
                 "WorkingSetBytes": ("memory", "Bytes", "average"),
                 "Requests": ("requests", "Count/min", "total")}
GCP_METRICS = {"container/cpu/utilizations": ("cpu_p50", "%", "ALIGN_PERCENTILE_50", "REDUCE_MEAN", 100),
               "container/memory/utilizations": ("memory_p50", "%", "ALIGN_PERCENTILE_50", "REDUCE_MEAN", 100),
               "request_count": ("requests", "Count/min", "ALIGN_SUM", "REDUCE_SUM", 1)}


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def validate_services(services):
    if not 2 <= len(services) <= 20 or len(set(services)) != len(services) or any(not NAME.fullmatch(item) for item in services):
        raise ValueError("Choose 2–20 different service names using letters, numbers, hyphens, or underscores")


def azure_url(subscription, group, service, onset):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", subscription) or not re.fullmatch(r"[A-Za-z0-9_.()-]{1,90}", group):
        raise ValueError("Use an Azure subscription UUID and resource group name")
    resource = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.App/containerApps/{service}"
    params = {"api-version": "2023-10-01", "metricnamespace": "Microsoft.App/containerapps",
              "metricnames": ",".join(AZURE_METRICS), "aggregation": "Average,Total", "interval": "PT1M",
              "timespan": f"{iso(onset - WINDOW_SECONDS)}/{iso(onset + WINDOW_SECONDS + 60)}"}
    return "https://management.azure.com" + resource + "/providers/Microsoft.Insights/metrics?" + urlencode(params)


def map_azure(document, service):
    if not isinstance(document, dict) or not isinstance(document.get("value"), list):
        raise ValueError(f"Azure metrics response for {service} has no value list")
    found = set()
    mapped = []
    for item in document["value"]:
        source_name = item.get("name", {}).get("value")
        if source_name not in AZURE_METRICS:
            continue
        if source_name in found:
            raise ValueError(f"Azure returned duplicate {source_name} for {service}")
        found.add(source_name)
        if item.get("errorCode") not in (None, "Success"):
            raise ValueError(f"Azure returned {item['errorCode']} for {service}.{source_name}")
        streams = item.get("timeseries", [])
        if len(streams) > 1:
            raise ValueError(f"Azure returned multiple dimension series for {service}.{source_name}; use rolled-up metrics")
        metric, unit, field = AZURE_METRICS[source_name]
        points = []
        for row in streams[0].get("data", []) if streams else []:
            if row.get(field) is not None:
                points.append([timestamp(row["timeStamp"]), numeric(row[field], source_name)])
        if points:
            mapped.append({"service": service, "metric": metric, "unit": unit, "points": points})
    return mapped


def gcp_url(project, region, service, source_name, onset, page_token=""):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", project) or not re.fullmatch(r"[a-z0-9-]{1,63}", region):
        raise ValueError("Invalid GCP project ID or region")
    _, _, aligner, reducer, _ = GCP_METRICS[source_name]
    filter_text = (f'metric.type="run.googleapis.com/{source_name}" AND resource.type="cloud_run_revision" '
                   f'AND resource.labels.service_name="{service}" AND resource.labels.location="{region}"')
    params = {"filter": filter_text, "interval.startTime": iso(onset - WINDOW_SECONDS),
              "interval.endTime": iso(onset + WINDOW_SECONDS + 60), "view": "FULL", "pageSize": 1000,
              "aggregation.alignmentPeriod": "60s", "aggregation.perSeriesAligner": aligner,
              "aggregation.crossSeriesReducer": reducer}
    if page_token:
        params["pageToken"] = page_token
    return f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries?" + urlencode(params)


def map_gcp(documents, service, source_name):
    metric, unit, _, _, multiplier = GCP_METRICS[source_name]
    streams = [item for document in documents for item in document.get("timeSeries", [])]
    if not streams:
        return None
    identities = {json.dumps([item.get("metric"), item.get("resource")], sort_keys=True) for item in streams}
    if len(identities) > 1:
        raise ValueError(f"GCP returned multiple reduced series for {service}.{metric}")
    points = []
    seen = set()
    for stream in streams:
        for point in stream.get("points", []):
            value = point.get("value", {})
            raw = value.get("doubleValue", value.get("int64Value"))
            if raw is None:
                raise ValueError(f"GCP returned a nonnumeric value for {service}.{metric}")
            time = timestamp(point["interval"]["endTime"])
            if time in seen:
                raise ValueError(f"GCP returned duplicate points for {service}.{metric}")
            seen.add(time)
            points.append([time, numeric(raw, metric) * multiplier])
    return {"service": service, "metric": metric, "unit": unit, "points": points} if points else None


def export_azure(args, onset):
    cli = shutil.which("az")
    if not cli:
        raise ValueError("Azure CLI is not installed or not on PATH")
    mapped = []
    for service in args.services:
        result = subprocess.run([cli, "rest", "--method", "get", "--url",
                                 azure_url(args.subscription, args.resource_group, service, onset), "--output", "json"],
                                capture_output=True, text=True, timeout=120, check=True)
        mapped.extend(map_azure(json.loads(result.stdout), service))
    return "Azure Container Apps", mapped


def export_gcp(args, onset):
    cli = shutil.which("gcloud")
    if not cli:
        raise ValueError("Google Cloud CLI is not installed or not on PATH")
    token = subprocess.run([cli, "auth", "print-access-token"], capture_output=True, text=True,
                           timeout=30, check=True).stdout.strip()
    if not token:
        raise ValueError("Google Cloud CLI did not return an access token")
    mapped = []
    for service in args.services:
        for source_name in GCP_METRICS:
            documents, page_token = [], ""
            for _ in range(20):
                url = gcp_url(args.project, args.region, service, source_name, onset, page_token)
                request = Request(url, headers={"Authorization": f"Bearer {token}", "X-Goog-User-Project": args.project})
                with urlopen(request, timeout=30) as response:
                    document = json.load(response)
                documents.append(document)
                page_token = document.get("nextPageToken", "")
                if not page_token:
                    break
            else:
                raise ValueError(f"GCP pagination exceeded 20 pages for {service}.{source_name}")
            item = map_gcp(documents, service, source_name)
            if item:
                mapped.append(item)
    return "GCP Cloud Run", mapped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="provider", required=True)
    azure = sub.add_parser("azure", help="Read Azure Container Apps through Azure Monitor")
    azure.add_argument("--subscription", required=True, help="Subscription UUID")
    azure.add_argument("--resource-group", required=True)
    gcp = sub.add_parser("gcp", help="Read Cloud Run through Cloud Monitoring")
    gcp.add_argument("--project", required=True, help="GCP project ID")
    gcp.add_argument("--region", required=True, help="Cloud Run region")
    for command, default in ((azure, "azure-containerapps-export.json"), (gcp, "gcp-cloudrun-export.json")):
        command.add_argument("--services", nargs="+", required=True)
        command.add_argument("--onset", required=True, help="Incident onset in UTC ISO 8601 or Unix seconds")
        command.add_argument("--output", type=Path, default=Path(default))
    args = parser.parse_args()
    try:
        validate_services(args.services)
        onset = timestamp(args.onset)
        provider, mapped = (export_azure(args, onset) if args.provider == "azure" else export_gcp(args, onset))
        payload = {"IncidentAtlasProvider": provider, "IncidentAtlasSeries": mapped}
        content = json.dumps(payload, separators=(",", ":"))
        analyze_cloud(content, onset, "Export validation")
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print(f"Saved {args.output}. Import it in Incident Atlas using the same onset: {iso(onset)}")
    except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        parser.exit(2, f"Export failed: {getattr(error, 'stderr', None) or error}\n")


if __name__ == "__main__":
    main()
