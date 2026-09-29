"""Analyze benchmark-shaped CSV or native-cadence cloud metrics sent on stdin."""

import csv
import hashlib
import io
import json
import re
import sys

import numpy as np
import pandas as pd
from joblib import load

from benchmark import MODEL_PATH, case_from_metrics, load_baro, rank_baro, rank_case, rank_fusion
from cloud_metrics import analyze_cloud, timestamp


METRIC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}_[A-Za-z][A-Za-z0-9-]{0,63}\Z")
MAX_INPUT_BYTES = 5_000_000
MAX_UNIX_SECONDS = 253402300799


def parse_csv(source, onset, name):
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("CSV is empty or exceeds 5 MB")
    if type(onset) is not int or not 0 < onset <= MAX_UNIX_SECONDS:
        raise ValueError("Fault onset must be a Unix timestamp in whole seconds")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(char) < 32 for char in name):
        raise ValueError("Incident name must be 1–80 printable characters")
    reader = csv.reader(io.StringIO(source.lstrip("\ufeff"), newline=""))
    header = next(reader, None)
    if not header or header[0] != "time" or not 3 <= len(header) <= 201 or len(set(header)) != len(header):
        raise ValueError("CSV needs a time column and 2–200 unique service_metric columns")
    if any(not METRIC_NAME.fullmatch(metric) for metric in header[1:]):
        raise ValueError("Metric headers must use service_metric names with letters, numbers, or hyphens")
    columns = {name: [] for name in header}
    for number, row in enumerate(reader, 2):
        if number > 3602:
            raise ValueError("CSV cannot exceed 3,601 data rows")
        if len(row) != len(header):
            raise ValueError(f"CSV row {number} has {len(row)} cells; expected {len(header)}")
        timestamp = row[0].strip()
        if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 12 or int(timestamp) > MAX_UNIX_SECONDS:
            raise ValueError(f"CSV row {number} has an invalid Unix timestamp")
        columns["time"].append(int(timestamp))
        for metric, cell in zip(header[1:], row[1:]):
            value = cell.strip()
            try:
                parsed = float(value) if value else np.nan
            except ValueError as error:
                raise ValueError(f"CSV row {number}, {metric} is not numeric") from error
            if value and not np.isfinite(parsed):
                raise ValueError(f"CSV row {number}, {metric} must be finite or blank")
            columns[metric].append(parsed)
    times = np.asarray(columns.pop("time"), dtype=np.int64)
    if len(times) < 601 or np.any(np.diff(times) != 1):
        raise ValueError("CSV needs at least 601 consecutive one-second rows")
    if onset < times[0] + 300 or onset > times[-1] - 300:
        raise ValueError("Fault onset needs 300 seconds of data before and after it")
    metrics = {metric: np.asarray(values, dtype=float) for metric, values in columns.items()}
    row = {"case": "upload-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:12], "system": "upload", "system_name": name.strip(), "fault": "unknown", "fault_description": "Imported metric window", "inject_time": onset}
    return row, times, metrics


def analyze(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request must be a JSON object")
    source, name = payload.get("content", payload.get("csv")), payload.get("name")
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("File is empty or exceeds 5 MB")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(char) < 32 for char in name):
        raise ValueError("Incident name must be 1–80 printable characters")
    onset = timestamp(payload.get("onset"))
    if source.lstrip("\ufeff \t\r\n").startswith(("{", "timestamp,")):
        return analyze_cloud(source, onset, name)
    row, times, metrics = parse_csv(source, onset, name)
    artifact = load(MODEL_PATH)
    if artifact.get("featureCount") != 7:
        raise ValueError("Trained model has an incompatible feature schema")
    case = case_from_metrics(row, times, ["time", *metrics], metrics.__getitem__)
    if len(case["services"]) < 2:
        raise ValueError("At least two services need usable metrics in the analysis window")
    case["truthRanks"] = rank_case(case, artifact["model"])
    frame = pd.DataFrame({"time": times, **metrics})
    rank_baro(case, load_baro(), table=frame)
    rank_fusion(case)
    for service in case["services"]:
        del service["features"]
    case["kind"] = "uploaded"
    if all(service["firstAnomalySeconds"] is None for service in case["services"]):
        case["signalWarning"] = "No service showed a sustained critical deviation in the first five minutes after onset. Treat this ranking as weak evidence."
    return {"kind": "analysis", "case": case, "modelTrainingCases": len(artifact["trainingCaseIds"]), "input": {"rows": len(times), "metrics": len(metrics)}}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Request exceeds 5 MB")
        result = analyze(json.loads(raw.decode("utf-8")))
        print(json.dumps(result, separators=(",", ":")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}))
        return 2
    except FileNotFoundError:
        print(json.dumps({"error": "Trained model missing; run python benchmark.py train"}))
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
