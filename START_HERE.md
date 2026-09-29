# Incident Atlas — start here

Incident Atlas can export AWS ECS, Azure Container Apps, or GCP Cloud Run metrics using your configured provider CLI. Import the JSON, inspect time-aligned change evidence, and export a report. A separate RCAEval benchmark retains the trained model and BARO comparison.

On Windows, from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
node server.mjs
```

Open [http://localhost:8000](http://localhost:8000). For AWS ECS, use `aws_ecs_export.py`; for Azure Container Apps or GCP Cloud Run, use `provider_export.py azure` or `provider_export.py gcp`. Each command needs at least two service names, a known UTC onset, and a configured provider CLI with metric read access. Import its JSON with the same onset. See [README.md](README.md) for exact commands and the long-form CSV option. Run `.\.venv\Scripts\python.exe -m unittest discover -v` for the shipped checks.

The 80.7% hybrid top-1 score comes from RCAEval, not cloud incidents. Cloud imports use a separate, unvalidated evidence ranking; the trained model and BARO are not applied to them. Green/yellow/red show statistical deviation from pre-onset readings, not confirmed service health or crash state.
