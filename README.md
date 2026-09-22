# Incident Atlas

Incident Atlas is a benchmark explorer for microservice root-cause ranking. It trains a small metric-based service ranker on [RCAEval](https://github.com/phamquiluan/RCAEval) RE1 cases and combines its service ranks with the published BARO method. The dashboard lets you inspect metric evidence and switch between the model, BARO, and their hybrid ranking.

The dashboard uses **real benchmark telemetry**. RCAEval cases are controlled fault injections, not production outages. A high rank is a hypothesis, not proof of cause.

## Run

On Windows, from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe benchmark.py all
.\.venv\Scripts\python.exe benchmark.py sensitivity
.\.venv\Scripts\python.exe benchmark.py validation
.\.venv\Scripts\python.exe -m unittest -v test_benchmark.py
node server.mjs
```

Run `.\.venv\Scripts\python.exe probe_ranker.py` to reproduce the nine-classifier development comparison; it does not change the benchmark bundles.

Open [http://localhost:8000](http://localhost:8000). The generated [web/benchmark.json](web/benchmark.json), [web/sensitivity.json](web/sensitivity.json), and [web/validation.json](web/validation.json) are included, so the dashboard can also run with just Node. `benchmark.py all` downloads the first 75 valid cases, trains three held-out fold models, and regenerates the primary JSON. `sensitivity` evaluates the next 75 cases; `validation` evaluates the remaining 223 valid cases. The model always trains on first selected cases from the *other two systems*. Raw Parquet stays in ignored `data/`; no API key is needed. Set `PORT` to use another local port.

The case picker covers Online Boutique, Sock Shop, and Train Ticket. Switch ranking methods and select a service to inspect its recorded metric and statistical deviation around the injected fault. The ranked lists and evaluation tables are saved outputs from the Python run, not browser-side inference. Reveal ground truth separately; the underlying dataset case names encode the answer, so this is a presentation aid rather than a blind challenge.

## Data and provenance

- **Source:** [RCAEval dataset mirror](https://huggingface.co/datasets/phamquiluan/RCAEval), revision `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`; [authors' code](https://github.com/phamquiluan/RCAEval).
- **License:** MIT for the dataset and authors' code, per the dataset card and repository. BARO is MIT licensed in RCAEval's third-party license list.
- **Selection:** the first two valid repetitions of each 5 injected services × 5 faults × 3 systems pair form the 150-case development set. The next two valid repetitions and the remaining valid repetition form the 223-case validation set. Case IDs, ranks, cohort IDs, and settings are in the three included JSON bundles. The validation model trains only on the first cohort from the other two systems.
- **Exclusions:** `re1ob_currencyservice_loss_1` has a malformed injection timestamp (`16933142`) before its recorded time range; repetition 2 takes its primary slot. `re1ob_productcatalogservice_cpu_3` also has an injection timestamp beyond its recorded end. These two pairs therefore have only four valid repetitions, leaving 73 rather than 75 cases in the last cohort.
- **Telemetry:** the original Parquet values are displayed without unit conversion. The dataset's wide metric table does not provide a per-column unit field; the UI therefore shows source metric names and leaves units unspecified. Null readings remain gaps in the chart.

## Methods

All methods receive the fault injection time and the same 300 seconds before and after it. This is a *localization* task with a known onset, not onset detection. For each metric, Incident Atlas computes a normal-window median and robust deviation scale:

```text
scale = max(1.4826 × MAD, 0.1 × standard deviation,
            0.01 × |median|, 0.000001)
deviation = min(|value − median| / scale, 50)
```

The **earliest-anomaly baseline** finds the first post-injection run of five consecutive seconds at deviation ≥6 for each metric, then ranks each service by its earliest metric. Ties favor more triggered metrics, then a larger peak deviation. No trigger receives a 301-second sentinel. The chart's green/yellow/red line uses deviation below 3, from 3 to below 6, and at least 6 respectively. These are statistical thresholds, not verified overload or crash thresholds.

The **trained ranker** aggregates seven case-local service features: first anomaly time, peak and top-three peak deviation, triggered metric count and fraction, maximum persistence, and metric count. It uses scikit-learn `ExtraTreesClassifier` with 100 trees, depth 5, minimum leaf size 3, balanced classes, and seed 7. It trains on two complete systems and predicts the third, rotating across all three systems. Each case's normal window supplies its own scale; no held-out label or case ID enters the features. Model scores are uncalibrated.

**BARO** is RCAEval 1.7.0's own `baro.py`, run on the same 10-minute slice. It uses the dataset card's forward-fill/back-fill/zero-fill preparation, then BARO's normal/faulty preprocessing and robust metric-score ranking. Metric ranks are deduplicated into service ranks; services filtered out by BARO are placed after its returned services. On Python 3.11, `benchmark.py` loads the upstream BARO module directly because RCAEval's package initializer eagerly imports unrelated optional methods on that version.

The **hybrid** gives each service `1 / model rank + 1 / BARO rank`, then sorts descending with service ID breaking ties. Its equal weights were selected after comparing nine small classifier variants and rank fusion on the first two repetitions. It **uses BARO as an input**; its gain is not evidence that the standalone trained model is conclusively better than BARO. The hybrid has BARO's computation cost plus the model's.

## Measured results

These are this project's results, **not** scores copied from the RCAEval paper. Top-1 and top-3 mean the annotated service appears within that many service positions. MRR is mean reciprocal rank. The first two repetitions were used for method selection; the later 223 cases test the fixed hybrid rule.

| Method | Development: first 75 top-1 | Development: next 75 top-1 | Validation: later 223 top-1 | Validation top-3 | Validation MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| Earliest anomaly | 40.0% | 37.3% | 35.0% | 72.2% | 0.562 |
| BARO | 68.0% | 69.3% | 73.5% | 96.0% | 0.847 |
| Trained model | 70.7% | 78.7% | 74.4% | 96.9% | 0.850 |
| Hybrid (model + BARO) | 77.3% | 85.3% | **80.7%** | 96.9% | 0.887 |

On validation, the hybrid–BARO top-1 difference is **+7.2 percentage points** with a paired 95% bootstrap interval of **+0.9 to +13.2 points**. Resampling keeps repeated injections of the same system × root service × fault pair together. The last 73 cases alone give hybrid **76.7%**, BARO **71.2%**, and standalone model **69.9%**; their smaller paired interval includes zero. Intervals do not cover uncertainty about entirely new systems or the exploratory choice of method.

The standalone model's validation difference from BARO is **+0.9 points**, with a paired interval spanning zero. A deeper Extra Trees candidate looked slightly better on the second repetition (61 rather than 59 top-1 cases), but fell to **73.3%** on the next 150 cases versus **76.7%** for the original model and **74.7%** for BARO. It was rejected.

| Held-out system | Cases | Earliest anomaly top-1 | BARO top-1 | Ranker top-1 |
| --- | ---: | ---: | ---: | ---: |
| Online Boutique | 25 | 56.0% | 76.0% | 68.0% |
| Sock Shop | 25 | 60.0% | 84.0% | 92.0% |
| Train Ticket | 25 | 4.0% | 44.0% | 52.0% |

Train Ticket remains the hardest held-out system. Results by injected fault and the current run's mean ranking times appear in the dashboard bundle. Timings exclude file reads and feature extraction, and BARO's call includes its own preprocessing, so they are not equal end-to-end latency comparisons.

## Limits and next steps

This is a metric-only study of controlled injections in three systems. It does not measure production reliability, time saved, or causal correctness. The hybrid can be wrong, and its gain relies on running BARO. Bootstrap intervals group repeated injections but cannot capture uncertainty about entirely new systems. The dashboard lists metric-bearing service candidates, not a verified dependency topology.

The next extension is a matched RE2 study with logs and traces where available, measuring whether each source improves held-out ranking before adding it to the UI. A user telemetry import should follow a validated schema and unit contract. Neither capability is claimed in this build.
