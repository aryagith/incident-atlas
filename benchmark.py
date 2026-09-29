"""Build the RCAEval RE1 benchmark and the dashboard's measured case bundle."""

import argparse
import importlib.util
import json
import os
import re
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from sklearn.ensemble import ExtraTreesClassifier


REVISION = "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e"
BASE = f"https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/{REVISION}/"
DATA = Path(__file__).parent / "data"
OUTPUT = Path(__file__).parent / "web" / "benchmark.json"
MODEL_PATH = Path(__file__).parent / "model" / "ranker.joblib"
WINDOW_SECONDS = 300
WARNING_Z = 3.0
CRITICAL_Z = 6.0
SUSTAINED_SECONDS = 5


def download(relative):
    if not re.fullmatch(r"[a-zA-Z0-9_./-]+", relative) or ".." in relative:
        raise ValueError(f"Invalid dataset path: {relative}")
    target = DATA / relative
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(BASE + relative, timeout=90) as response, temporary.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            os.replace(temporary, target)
            return target
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def selected_cases(excluded=None, expected=75):
    index = pq.read_table(download("cases.parquet")).to_pylist()
    cases = []
    seen = set()
    excluded = excluded or set()
    for row in sorted(index, key=lambda item: item["repetition"]):
        if row["suite"] != "RE1" or row["case"] in excluded or row["normal_timesteps"] < 60 or not row["time_start"] < row["inject_time"] < row["time_end"]:
            continue
        key = (row["system"], row["root_cause_service"], row["fault"])
        if key not in seen:
            cases.append(row)
            seen.add(key)
    if len(cases) != expected:
        raise ValueError(f"Expected {expected} RE1 fault/service pairs, found {len(cases)}")
    return sorted(cases, key=lambda row: row["case"])


def selected_cohorts():
    used = set()
    cohorts = []
    for expected in (75, 75, 75, 75, 73):
        rows = selected_cases(used, expected=expected)
        cohorts.append(rows)
        used.update(row["case"] for row in rows)
    return cohorts


def fetch(cases):
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(download, f"{row['case']}/metrics.parquet"): row["case"] for row in cases}
        for number, future in enumerate(as_completed(futures), 1):
            future.result()
            if number % 15 == 0 or number == len(futures):
                print(f"Ready {number}/{len(futures)} case metric files", flush=True)


def severity(value, warning=WARNING_Z, critical=CRITICAL_Z):
    return "critical" if value >= critical else "warning" if value >= warning else "normal"


def clean_number(value):
    return float(f"{value:.6g}") if np.isfinite(value) else None


def read_case(row):
    table = pq.read_table(DATA / row["case"] / "metrics.parquet")
    return case_from_metrics(row, table["time"].to_numpy(), table.column_names, lambda name: table[name].to_numpy())


def case_from_metrics(row, timestamps, metric_names, values_for):
    timestamps = np.asarray(timestamps, dtype=np.int64)
    onset = row["inject_time"]
    window = (timestamps >= onset - WINDOW_SECONDS) & (timestamps <= onset + WINDOW_SECONDS)
    times = timestamps[window]
    normal_mask = times < onset
    faulty_mask = times >= onset
    if normal_mask.sum() < 60 or faulty_mask.sum() < 60:
        raise ValueError(f"Insufficient normal/faulty readings in {row['case']}")
    sample_indices = np.unique(np.linspace(0, len(times) - 1, 121, dtype=int))
    grouped = defaultdict(lambda: {"metrics": [], "best": None})

    for metric in metric_names:
        if metric == "time" or "_" not in metric:
            continue
        service = metric.rsplit("_", 1)[0]
        values = np.asarray(values_for(metric), dtype=float)[window]
        baseline = values[normal_mask]
        baseline = baseline[np.isfinite(baseline)]
        if len(baseline) < 60:
            continue
        middle = float(np.median(baseline))
        mad = float(np.median(np.abs(baseline - middle)))
        scale = max(1.4826 * mad, float(np.std(baseline)) * 0.1, abs(middle) * 0.01, 1e-6)
        z = np.nan_to_num(np.abs(values - middle) / scale, nan=0.0, posinf=50.0)
        z = np.minimum(z, 50.0)
        post_z = z[faulty_mask]
        consecutive = np.convolve((post_z >= CRITICAL_Z).astype(int), np.ones(SUSTAINED_SECONDS, dtype=int), "valid")
        hits = np.flatnonzero(consecutive == SUSTAINED_SECONDS)
        first = int(times[faulty_mask][hits[0]] - onset) if len(hits) else WINDOW_SECONDS + 1
        peak = float(np.max(post_z))
        persistence = float(np.mean(post_z >= CRITICAL_Z))
        info = {"metric": metric, "first": first, "peak": peak, "persistence": persistence}
        entry = grouped[service]
        entry["metrics"].append(info)
        key = (first, -peak)
        if entry["best"] is None or key < entry["best"][0]:
            entry["best"] = (key, metric, values[sample_indices], z[sample_indices])

    services = []
    for name, entry in grouped.items():
        metrics = entry["metrics"]
        first = min(item["first"] for item in metrics)
        peaks = sorted((item["peak"] for item in metrics), reverse=True)
        triggered = sum(item["first"] <= WINDOW_SECONDS for item in metrics)
        features = [first, peaks[0], float(np.mean(peaks[:3])), triggered, triggered / len(metrics), max(item["persistence"] for item in metrics), len(metrics)]
        _, metric, raw, z = entry["best"]
        services.append({
            "id": name,
            "name": name,
            "metric": metric,
            "unit": None,
            "values": [clean_number(value) for value in raw],
            "severityScores": [clean_number(score) if np.isfinite(value) else None for value, score in zip(raw, z)],
            "thresholds": {"warning": WARNING_Z, "critical": CRITICAL_Z},
            "thresholdUnit": "robust σ",
            "status": severity(float(z[-1])) if np.isfinite(raw[-1]) else "unknown",
            "firstAnomalySeconds": first if first <= WINDOW_SECONDS else None,
            "peakDeviation": clean_number(peaks[0]),
            "features": features,
        })
    if not services:
        raise ValueError(f"No usable service metrics in {row['case']}")
    truth = row.get("root_cause_service")
    if truth is not None and truth not in {service["id"] for service in services}:
        raise ValueError(f"Ground-truth service lacks metrics in {row['case']}: {truth}")
    return {
        "id": row["case"],
        "system": row["system"],
        "systemName": row["system_name"],
        "fault": row["fault"],
        "faultDescription": row["fault_description"],
        "groundTruth": truth,
        "onset": onset,
        "time": [int(value) for value in times[sample_indices]],
        "onsetIndex": int(np.argmin(np.abs(times[sample_indices] - onset))),
        "services": services,
    }


def rank_case(case, model):
    services = case["services"]
    start = time.perf_counter()
    baseline = sorted(services, key=lambda item: (item["features"][0], -item["features"][3], -item["features"][1], item["id"]))
    baseline_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    scores = model.predict_proba([item["features"] for item in services])[:, list(model.classes_).index(1)]
    model_ms = (time.perf_counter() - start) * 1000
    for service, score in zip(services, scores):
        service["modelScore"] = clean_number(float(score))
    ranked = sorted(services, key=lambda item: (-item["modelScore"], item["id"]))
    for position, service in enumerate(baseline, 1):
        service["baselineRank"] = position
    for position, service in enumerate(ranked, 1):
        service["modelRank"] = position
    case["rankTimeMs"] = {"baseline": clean_number(baseline_ms), "model": clean_number(model_ms)}
    case["services"] = ranked
    if case["groundTruth"] is None:
        return {}
    return {"baseline": next(item["baselineRank"] for item in services if item["id"] == case["groundTruth"]), "model": next(item["modelRank"] for item in services if item["id"] == case["groundTruth"])}


def load_baro():
    import RCAEval

    # RCAEval 1.7 eagerly loads optional methods on Python 3.11; load its BARO module directly.
    path = Path(RCAEval.__file__).parent / "e2e" / "baro.py"
    spec = importlib.util.spec_from_file_location("rcaeval_baro", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.baro


def rank_baro(case, baro, table=None):
    table = table if table is not None else pq.read_table(DATA / case["id"] / "metrics.parquet").to_pandas()
    onset = case["onset"]
    window = table[(table["time"] >= onset - WINDOW_SECONDS) & (table["time"] <= onset + WINDOW_SECONDS)]
    window = window.ffill().bfill().fillna(0.0)
    start = time.perf_counter()
    metric_ranks = baro(window, onset, dataset="user" if case["system"] == "upload" else f"re1-{case['system']}")["ranks"]
    elapsed = (time.perf_counter() - start) * 1000
    candidates = {service["id"] for service in case["services"]}
    ordered = list(dict.fromkeys(metric.rsplit("_", 1)[0] for metric in metric_ranks if "_" in metric and metric.rsplit("_", 1)[0] in candidates))
    ordered += sorted(candidates - set(ordered))
    positions = {service: index for index, service in enumerate(ordered, 1)}
    for service in case["services"]:
        service["baroRank"] = positions[service["id"]]
    case["rankTimeMs"]["baro"] = clean_number(elapsed)
    if case["groundTruth"] is not None:
        case["truthRanks"]["baro"] = positions[case["groundTruth"]]


def rank_fusion(case):
    start = time.perf_counter()
    ranked = sorted(case["services"], key=lambda service: (-(1 / service["modelRank"] + 1 / service["baroRank"]), service["id"]))
    for position, service in enumerate(ranked, 1):
        service["fusionRank"] = position
    case["rankTimeMs"]["fusion"] = clean_number((time.perf_counter() - start) * 1000)
    if case["groundTruth"] is not None:
        case["truthRanks"]["fusion"] = next(service["fusionRank"] for service in ranked if service["id"] == case["groundTruth"])


def train_deployment(rows):
    from joblib import dump

    cases = [read_case(row) for row in rows]
    x = [service["features"] for case in cases for service in case["services"]]
    y = [int(service["id"] == case["groundTruth"]) for case in cases for service in case["services"]]
    model = ExtraTreesClassifier(n_estimators=100, max_depth=5, min_samples_leaf=3, class_weight="balanced", random_state=7, n_jobs=-1)
    model.fit(x, y)
    artifact = {"model": model, "datasetRevision": REVISION, "trainingCaseIds": [case["id"] for case in cases], "featureCount": 7}
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_PATH.with_suffix(".joblib.tmp")
    dump(artifact, temporary, compress=3)
    os.replace(temporary, MODEL_PATH)
    print(f"Wrote {MODEL_PATH} using {len(cases)} labeled cases")


def summarize(cases, method):
    ranks = np.array([case["truthRanks"][method] for case in cases], dtype=float)
    rng = np.random.default_rng(7)
    samples = rng.choice(ranks <= 1, size=(2000, len(ranks)), replace=True).mean(axis=1)
    return {
        "cases": len(cases),
        "top1": clean_number(np.mean(ranks <= 1)),
        "top3": clean_number(np.mean(ranks <= 3)),
        "mrr": clean_number(np.mean(1 / ranks)),
        "top1Ci95": [clean_number(value) for value in np.quantile(samples, [0.025, 0.975])],
        "meanRankTimeMs": clean_number(np.mean([case["rankTimeMs"][method] for case in cases])),
    }


def paired_summary(cases, first, second):
    groups = defaultdict(list)
    for case in cases:
        key = (case["system"], case["groundTruth"], case["fault"])
        groups[key].append(int(case["truthRanks"][first] == 1) - int(case["truthRanks"][second] == 1))
    sums = np.array([sum(values) for values in groups.values()])
    counts = np.array([len(values) for values in groups.values()])
    sampled = np.random.default_rng(7).integers(0, len(groups), size=(2000, len(groups)))
    bootstrap = sums[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    return {"difference": clean_number(sums.sum() / counts.sum()), "ci95": [clean_number(value) for value in np.quantile(bootstrap, [0.025, 0.975])], "resamplingUnit": "system × root service × fault"}


def build(rows, training_rows=None, output=OUTPUT, compact=False, selection=None, cohorts=None, development_rows=None, detail_output=None):
    cases = [read_case(row) for row in rows]
    training_cases = [read_case(row) for row in training_rows] if training_rows is not None else cases
    for held_out in sorted({case["system"] for case in cases}):
        training = [case for case in training_cases if case["system"] != held_out]
        x = [service["features"] for case in training for service in case["services"]]
        y = [int(service["id"] == case["groundTruth"]) for case in training for service in case["services"]]
        model = ExtraTreesClassifier(n_estimators=100, max_depth=5, min_samples_leaf=3, class_weight="balanced", random_state=7, n_jobs=-1)
        model.fit(x, y)
        for case in cases:
            if case["system"] == held_out:
                case["truthRanks"] = rank_case(case, model)
    baro = load_baro()
    for number, case in enumerate(cases, 1):
        rank_baro(case, baro)
        rank_fusion(case)
        if number % 15 == 0:
            print(f"BARO evaluated {number}/{len(cases)} cases", flush=True)
    methods = ("baseline", "baro", "model", "fusion")
    evaluation = {method: summarize(cases, method) for method in methods}
    evaluation["modelVsBaroTop1"] = paired_summary(cases, "model", "baro")
    evaluation["fusionVsBaroTop1"] = paired_summary(cases, "fusion", "baro")
    evaluation["fusionVsModelTop1"] = paired_summary(cases, "fusion", "model")
    by_system = {system: {method: summarize([case for case in cases if case["system"] == system], method) for method in methods} for system in sorted({case["system"] for case in cases})}
    by_fault = {fault: {method: summarize([case for case in cases if case["fault"] == fault], method) for method in methods} for fault in sorted({case["fault"] for case in cases})}
    for case in cases:
        for service in case["services"]:
            del service["features"]
    result = {
        "kind": "real-benchmark",
        "source": "RCAEval RE1",
        "revision": REVISION,
        "selection": selection or ("next unused valid repetition for every system × root-cause service × fault" if compact else "first valid repetition for every system × root-cause service × fault; re1ob_currencyservice_loss_1 excluded for malformed injection time, replaced by repetition 2"),
        "method": {"normalWindowSeconds": WINDOW_SECONDS, "faultWindowSeconds": WINDOW_SECONDS, "warningRobustZ": WARNING_Z, "criticalRobustZ": CRITICAL_Z, "sustainedSeconds": SUSTAINED_SECONDS, "model": "ExtraTreesClassifier; 100 trees; max_depth=5; min_samples_leaf=3; balanced classes; seed=7", "publishedBaseline": "RCAEval 1.7.0 BARO", "fusion": "equal-weight reciprocal ranks of model and BARO; ties by service ID", "split": "leave-one-system-out", "trainingCases": "first valid repetitions"},
        "evaluation": evaluation,
        "bySystem": by_system,
        "byFault": by_fault,
        **({"trainingCaseIds": [case["id"] for case in training_cases]} if compact else {}),
        **({"developmentCaseIds": [row["case"] for row in development_rows]} if development_rows else {}),
        "cases": [{"id": case["id"], "system": case["system"], "fault": case["fault"], "rootService": case["groundTruth"], "truthRanks": case["truthRanks"]} for case in cases] if compact else cases,
    }
    if cohorts:
        result["cohorts"] = {}
        for name, cohort_rows in cohorts.items():
            ids = {row["case"] for row in cohort_rows}
            subset = [case for case in cases if case["id"] in ids]
            result["cohorts"][name] = {"caseIds": sorted(ids), "evaluation": {method: summarize(subset, method) for method in methods}, "fusionVsBaroTop1": paired_summary(subset, "fusion", "baro")}
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, output)
    if detail_output:
        detail_temporary = detail_output.with_suffix(".json.tmp")
        detail_temporary.write_text(json.dumps({"kind": "validation-cases", "cases": cases}, separators=(",", ":")), encoding="utf-8")
        os.replace(detail_temporary, detail_output)
    print(json.dumps({"cases": len(cases), "evaluation": evaluation, "bySystem": {key: {method: summary["top1"] for method, summary in value.items()} for key, value in by_system.items()}}, indent=2))
    print(f"Wrote {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "build", "sensitivity", "validation", "train", "all"))
    args = parser.parse_args()
    rows = selected_cases()
    if args.command in ("fetch", "all"):
        fetch(rows)
    if args.command in ("build", "all"):
        build(rows)
    if args.command in ("sensitivity", "validation", "train", "all"):
        first, second, third, fourth, last = selected_cohorts()
        if args.command in ("sensitivity", "all"):
            fetch(second)
            build(second, training_rows=first, output=OUTPUT.with_name("sensitivity.json"), compact=True)
        if args.command in ("validation", "all"):
            final = third + fourth + last
            fetch(final)
            build(final, training_rows=first, output=OUTPUT.with_name("validation.json"), compact=True, selection="third and fourth valid repetitions for all 75 pairs, plus the last valid repetition for 73 pairs; equal-weight fusion chosen on first and second repetitions", cohorts={"third": third, "fourth": fourth, "last": last}, development_rows=second, detail_output=OUTPUT.with_name("validation-cases.json"))
        if args.command in ("train", "all"):
            training = first + second + third + fourth + last
            fetch(training)
            train_deployment(training)


if __name__ == "__main__":
    main()
