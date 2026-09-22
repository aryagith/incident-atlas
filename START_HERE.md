# Incident Atlas development notes

The original handoff scaffold has been replaced by the metric-only RCAEval benchmark demo. [README.md](README.md) is the runbook, method description, measured results, provenance record, and limitation statement.

## Current product

- `benchmark.py all` fetches 75 selected RE1 cases, computes an earliest-anomaly baseline, runs RCAEval's BARO, trains an ExtraTrees service ranker, evaluates each system as a held-out test fold, and writes `web/benchmark.json`. `sensitivity` covers the next 75 cases; `validation` covers 223 later cases. The equal-weight model/BARO hybrid was chosen on the first two cohorts and evaluated on the later ones.
- `web/` loads the measured bundles. A user can choose a real case, switch between hybrid, model, and BARO rankings, inspect a recorded metric timeline, reveal ground truth, and compare measured results. The service list is a candidate ranking, not a topology.
- The visual system is black and grey. Green/yellow/red on metric traces represents normal/warning/critical *statistical deviation* from that case's pre-fault baseline; it does not assert actual overload or crash.
- The old synthetic fixture has been removed; the dashboard loads only the measured benchmark bundle.

## What remains

- Test on genuinely new systems or operational data before claiming broader reliability; the RE1 repetitions have now been exhausted except for two malformed cases.
- Extend to RE2 logs and traces only where those sources exist, and compare each source combination on matched held-out cases. Do not claim multimodal gains without measured ablation.
- Add real dependency topology only when graph relationships can be sourced. A metric-bearing service list is not a dependency map.
- Design a validated user telemetry import with clear timestamp, missing-value, and unit rules. No arbitrary production integration is required for the benchmark demo.
- Do not claim production reliability, causal proof, or incident-response time saved from controlled fault injections.

The standalone ranker remains close to BARO. The hybrid scores higher on later repetitions, but it uses BARO as an input and the study covers only three benchmark systems.
