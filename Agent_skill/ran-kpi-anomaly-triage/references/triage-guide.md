# RAN KPI Triage Guide

Use the script score as a prioritization aid, not as the only source of truth.

Severity interpretation:

- `critical`: immediate NOC review; likely service impact or multi-KPI degradation.
- `major`: probable degradation; compare to baseline and escalate if sustained.
- `minor`: weak anomaly; monitor and correlate with nearby alarms.
- `normal`: no configured threshold breached.

KPI driver hints:

- Low availability usually points to node, sector, carrier, maintenance, or power issues.
- High drop rate usually points to radio quality, handover, congestion, or admission problems.
- High latency and packet loss often point to transport/backhaul or upstream queueing.
- Low throughput should be checked together with PRB utilization, active users, and scheduler counters.
- High affected subscriber count should raise escalation priority even when raw KPI values are only major.

When reporting to an operator, include the top cell, top site, KPI drivers, and the first team that should inspect the issue.
