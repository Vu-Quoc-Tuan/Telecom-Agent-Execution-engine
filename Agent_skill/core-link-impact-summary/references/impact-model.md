# Core Link Impact Model

The impact score intentionally favors operational blast radius:

- Critical and major alarms add the largest base score.
- Multiple affected services increase priority because customer-facing scope is broader.
- Multiple sites increase priority because redundancy or upstream aggregation may be involved.
- Latency above 100 ms and packet loss above 2 percent add degradation pressure.
- Throughput below 100 Mbps adds evidence of congestion or path impairment.

Interpretation:

- `high`: likely incident-worthy; validate redundancy and engage transport/core team.
- `medium`: degradation likely; correlate with route changes, interface errors, and service tickets.
- `low`: monitor unless it repeats or coincides with customer impact.

When explaining results, avoid claiming root cause. Say the score indicates likely operational impact and list the KPI or alarm drivers that support that priority.
