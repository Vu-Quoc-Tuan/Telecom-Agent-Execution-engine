---
name: ran-kpi-anomaly-triage
description: Rank and explain RAN cell KPI anomalies for NOC triage. Use when operators need to inspect radio access network KPI records, cell or gNodeB/eNodeB alarms, latency, packet_loss, throughput, availability, drop-rate, congestion, affected subscribers, or service degradation before escalation.
metadata:
  version: "1.0.0"
---

# RAN KPI Anomaly Triage

Use this skill to turn a batch of RAN KPI observations into a prioritized NOC triage list. It is designed for cell, sector, eNodeB, and gNodeB incidents where the operator has KPI samples but needs a quick severity ranking and concrete next actions.

## Workflow

1. Gather KPI records from the user, monitoring output, or an alarm export. Prefer fields such as `cell_id`, `site_id`, `node`, `availability_pct`, `drop_rate_pct`, `latency_ms`, `packet_loss_pct`, `throughput_mbps`, `affected_subscribers`, and `alarm_count`.
2. If records are provided, run `scripts/triage_ran_kpis.py` through `run_skill_script` with:

```json
{
  "records": [
    {
      "cell_id": "HNI-LTE-001-A",
      "site_id": "HNI001",
      "node": "HNI-eNB-001",
      "availability_pct": 96.2,
      "drop_rate_pct": 4.8,
      "latency_ms": 112,
      "packet_loss_pct": 2.4,
      "throughput_mbps": 18,
      "affected_subscribers": 840,
      "alarm_count": 5
    }
  ]
}
```

3. If no records are available, run the script with `{}` to generate a smoke-test triage from bundled sample data and explain that live input is still needed for production conclusions.
4. Summarize the returned JSON in operator language: highest-risk cells first, why each cell was ranked, and what the NOC should check next.

## Script

`scripts/triage_ran_kpis.py` reads `args.json` and prints a JSON object containing:

- `summary`: counts by severity and the top site.
- `ranked_cells`: each cell with a numeric score, severity, drivers, and recommended actions.
- `operator_notes`: concise notes for escalation or follow-up.

The script uses only Python standard library and does not require network or database access, so upload smoke tests can run safely in the Docker sandbox.

## Reference

Read `references/triage-guide.md` when you need the exact scoring interpretation or want to explain why a KPI triggered a given severity.
