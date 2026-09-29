"""Native-cadence evidence ranking for CloudWatch results and long-form metric CSVs."""

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import datetime

import numpy as np

from benchmark import clean_number


NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_UNIX_SECONDS = 253402300799
WINDOW_SECONDS = 1800


def timestamp(value):
    if type(value) is int or (isinstance(value, str) and value.isascii() and value.isdecimal()):
        seconds = int(value)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError) as error:
            raise ValueError(f"Invalid timestamp: {value}") from error
        if parsed.tzinfo is None:
            raise ValueError("Timestamps need a timezone, such as Z")
        seconds = parsed.timestamp()
        if not seconds.is_integer():
            raise ValueError("Timestamps must use whole seconds")
        seconds = int(seconds)
    else:
        raise ValueError(f"Invalid timestamp: {value}")
    if not 0 < seconds <= MAX_UNIX_SECONDS:
        raise ValueError("Timestamp is outside the supported Unix range")
    return seconds


def numeric(value, context):
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} must be numeric") from error
    if not np.isfinite(result):
        raise ValueError(f"{context} must be finite")
    return result


def add_point(series, service, metric, time, value, unit=None):
    if not NAME.fullmatch(service) or not NAME.fullmatch(metric):
        raise ValueError("Service and metric names must use letters, numbers, hyphens, or underscores")
    key = (service, metric)
    entry = series.setdefault(key, {"points": {}, "unit": unit})
    if time in entry["points"]:
        raise ValueError(f"Duplicate timestamp for {service}.{metric}")
    if entry["unit"] != unit:
        raise ValueError(f"Mixed units for {service}.{metric}")
    entry["points"][time] = value


def parse_cloudwatch(source):
    try:
        document = json.loads(source.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ValueError("CloudWatch export is not valid JSON") from error
    if not isinstance(document, dict) or not isinstance(document.get("MetricDataResults"), list):
        raise ValueError("Expected CloudWatch GetMetricData JSON with MetricDataResults")
    if document.get("NextToken"):
        raise ValueError("CloudWatch response is incomplete; export all pages with the AWS CLI")
    units = document.get("IncidentAtlasUnits", {})
    if not isinstance(units, dict):
        raise ValueError("IncidentAtlasUnits must map metric labels to units")
    series = {}
    for result in document["MetricDataResults"]:
        if not isinstance(result, dict):
            raise ValueError("CloudWatch MetricDataResults must contain objects")
        label = result.get("Label")
        if not isinstance(label, str) or "." not in label:
            raise ValueError("Set each CloudWatch query Label to service.metric, such as checkout.cpu")
        service, metric = label.rsplit(".", 1)
        if result.get("StatusCode", "Complete") != "Complete":
            raise ValueError(f"CloudWatch series {label} is incomplete: {result.get('StatusCode')}")
        times, values = result.get("Timestamps"), result.get("Values")
        if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
            raise ValueError(f"CloudWatch series {label} has mismatched timestamps and values")
        unit = units.get(label)
        if unit is not None and (not isinstance(unit, str) or len(unit) > 24):
            raise ValueError(f"CloudWatch series {label} has an invalid unit")
        for raw_time, raw_value in zip(times, values):
            add_point(series, service, metric, timestamp(raw_time), numeric(raw_value, label), unit)
    return series, "AWS CloudWatch"


def parse_long_csv(source):
    reader = csv.DictReader(io.StringIO(source.lstrip("\ufeff"), newline=""))
    if reader.fieldnames not in (["timestamp", "service", "metric", "value"], ["timestamp", "service", "metric", "value", "unit"]):
        raise ValueError("Long CSV columns must be timestamp,service,metric,value[,unit]")
    series = {}
    for number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"CSV row {number} has the wrong number of cells")
        service, metric = row["service"].strip(), row["metric"].strip()
        unit = row.get("unit", "").strip() or None
        add_point(series, service, metric, timestamp(row["timestamp"].strip()), numeric(row["value"].strip(), f"CSV row {number}"), unit)
    return series, "Long-form CSV"


def analyze_cloud(source, onset, name):
    series, source_name = parse_cloudwatch(source) if source.lstrip("\ufeff \t\r\n").startswith("{") else parse_long_csv(source)
    if not 2 <= len(series) <= 200:
        raise ValueError("Import needs 2–200 metric series across at least two services")
    grouped = defaultdict(list)
    all_times = set()
    cadences = []
    reporting_services = set()
    for (service, metric), entry in series.items():
        points = sorted((time, value) for time, value in entry["points"].items() if onset - WINDOW_SECONDS <= time <= onset + WINDOW_SECONDS)
        pre = np.array([value for time, value in points if time < onset], dtype=float)
        post = [(time, value) for time, value in points if time >= onset]
        if len(pre) < 10:
            continue
        times = np.array([time for time, _ in points], dtype=np.int64)
        values = np.array([value for _, value in points], dtype=float)
        cadence = float(np.median(np.diff(times)))
        if cadence <= 0:
            continue
        cadences.append(cadence)
        middle = float(np.median(pre))
        scale = max(1.4826 * float(np.median(np.abs(pre - middle))), 0.1 * float(np.std(pre)), abs(middle) * 0.01, 1e-6)
        z = np.minimum(np.abs(values - middle) / scale, 50.0)
        after = np.flatnonzero(times >= onset)
        first = next((int(times[left] - onset) for left, right in zip(after, after[1:]) if z[left] >= 6 and z[right] >= 6 and times[right] - times[left] <= 2.5 * cadence), None)
        peak = float(max(z[after])) if len(after) else 0.0
        sparse = len(after) < 5
        if not sparse:
            reporting_services.add(service)
        shown = np.unique(np.linspace(0, len(times) - 1, min(121, len(times)), dtype=int))
        grouped[service].append({
            "id": service, "name": service, "metric": f"{service}.{metric}", "unit": entry["unit"],
            "times": [int(value) for value in times[shown]], "values": [clean_number(value) for value in values[shown]],
            "severityScores": [clean_number(value) for value in z[shown]],
            "thresholds": {"warning": 3, "critical": 6}, "thresholdUnit": "robust σ",
            "status": "missing" if sparse else "critical" if z[-1] >= 6 else "warning" if z[-1] >= 3 else "normal",
            "firstAnomalySeconds": first, "peakDeviation": clean_number(peak), "insufficientPostSamples": sparse,
            "preSamples": len(pre), "postSamples": len(post),
        })
        all_times.update(times.tolist())
    if len(grouped) < 2 or not reporting_services:
        raise ValueError("Need two services with 10 pre-onset readings, and at least one service with 5 post-onset readings within 30 minutes on each side")
    services = []
    for name_of_service, metrics in grouped.items():
        chosen = min(metrics, key=lambda item: (not item["insufficientPostSamples"], item["firstAnomalySeconds"] if item["firstAnomalySeconds"] is not None else float("inf"), -item["peakDeviation"], item["metric"]))
        chosen["triggeredMetrics"] = sum(item["firstAnomalySeconds"] is not None for item in metrics)
        chosen["missingMetrics"] = sum(item["insufficientPostSamples"] for item in metrics)
        chosen["metricCount"] = len(metrics)
        services.append(chosen)
    services.sort(key=lambda item: (not item["insufficientPostSamples"], item["firstAnomalySeconds"] if item["firstAnomalySeconds"] is not None else float("inf"), -item["missingMetrics"], -item["triggeredMetrics"], -item["peakDeviation"], item["id"]))
    for rank, service in enumerate(services, 1):
        service["evidenceRank"] = rank
    bounds = [min(all_times), max(all_times)]
    case = {
        "id": "cloud-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:12],
        "kind": "cloud", "source": source_name, "system": "cloud", "systemName": name.strip(),
        "fault": "unknown", "faultDescription": "Imported service metrics", "groundTruth": None,
        "onset": onset, "time": bounds, "onsetIndex": 0, "services": services, "truthRanks": {},
        "cadenceSeconds": round(float(np.median(cadences))),
    }
    if all(service["firstAnomalySeconds"] is None and not service["insufficientPostSamples"] for service in services):
        case["signalWarning"] = "No service had two consecutive critical deviations. This ranking is based on peak changes and is weak evidence."
    return {"kind": "analysis", "case": case, "input": {"rows": sum(len(entry["points"]) for entry in series.values()), "metrics": len(series)}}
