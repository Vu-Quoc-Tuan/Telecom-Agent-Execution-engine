---
name: core-link-impact-summary
description: Summarize telecom core network link and interface alarm impact for NOC decisions. Use when operators provide link-down, interface, router, switch, latency, packet_loss, throughput, service, site, or cluster alarm records and need blast-radius ranking, affected services, and escalation recommendations.
metadata:
  version: "1.0.0"
---

# Core Link Impact Summary

Use this skill when a NOC operator has multiple transport/core alarms and needs a concise impact view: which link or site is most urgent, which services are affected, and what action should happen next.

## Workflow

1. Collect alarm records. Preferred fields are `alarm_id`, `link_id`, `site_id`, `device`, `interface`, `severity`, `service`, `latency_ms`, `packet_loss_pct`, `throughput_mbps`, and `timestamp`.
2. Run `scripts/summarize_link_impact.py` via `run_skill_script`.
3. Pass records in `args.records`; if none are provided, the script uses safe sample data for smoke testing.

Example arguments:

```json
{
  "records": [
    {
      "alarm_id": "a-101",
      "link_id": "HNI-SGN-CORE-01",
      "site_id": "HNI001",
      "device": "HNI-CORE-R01",
      "interface": "xe-0/0/1",
      "severity": "critical",
      "service": "mobile-data",
      "latency_ms": 145,
      "packet_loss_pct": 4.1,
      "throughput_mbps": 120,
      "timestamp": "2026-07-06T10:02:00Z"
    }
  ]
}
```

## Output

The script prints JSON with:

- `summary`: record count, impacted links, impacted sites, and severity mix.
- `impacts`: link/site groups ordered by impact score.
- `recommendations`: NOC-facing next steps.

Use the output to answer in plain language. Mention the top impacted link, services affected, severity drivers, and whether the issue looks like link failure, degradation, or broad site impact.

## Reference

Read `references/impact-model.md` when you need to explain how the impact score is calculated or tune the interpretation for local NOC rules.
