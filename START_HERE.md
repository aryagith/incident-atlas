# Incident Atlas — start here

Incident Atlas is a complete local app for ranking likely root-cause services from a metrics CSV around a known incident onset. It includes a trained model, BARO comparison, evidence charts, a Markdown report export, a real sample CSV, and a reproducible RCAEval benchmark.

On Windows, from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
node server.mjs
```

Open [http://localhost:8000](http://localhost:8000), click **Analyze your metrics**, then **Try real sample**. The sample should rank `adservice` first. To use your own file, see the CSV schema and limits in [README.md](README.md). Run `.\.venv\Scripts\python.exe -m unittest discover -v` for the shipped checks.

The published 80.7% hybrid top-1 score comes from 223 later RCAEval fault injections in the same three systems; it is not a measured accuracy claim for imported incidents. The deployable model was retrained on all 373 valid RE1 cases. Green/yellow/red show statistical deviation from the incident's pre-onset baseline, not confirmed service health or crash state.
