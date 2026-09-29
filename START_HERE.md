# Incident Atlas — start here

Incident Atlas has a practical AWS ECS path: export CPU and memory series with your configured AWS CLI, import the CloudWatch JSON, inspect native-cadence change evidence, and export a report. A separate RCAEval benchmark retains the trained model and BARO comparison.

On Windows, from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
node server.mjs
```

Open [http://localhost:8000](http://localhost:8000). For a real ECS incident, run `.\.venv\Scripts\python.exe aws_ecs_export.py --cluster YOUR_CLUSTER --services SERVICE_A SERVICE_B --onset 2026-09-29T14:00:00Z --region YOUR_REGION`, then import `cloudwatch-export.json` with the same onset. The AWS CLI needs a configured profile with `cloudwatch:GetMetricData`. See [README.md](README.md) for details and the long-form CSV option. Run `.\.venv\Scripts\python.exe -m unittest discover -v` for the shipped checks.

The 80.7% hybrid top-1 score comes from RCAEval, not AWS incidents. Cloud imports use a separate, unvalidated evidence ranking; the trained model and BARO are not applied to them. Green/yellow/red show statistical deviation from pre-onset readings, not confirmed service health or crash state.
