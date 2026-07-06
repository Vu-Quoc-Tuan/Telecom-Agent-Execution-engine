import json


DEFAULT_RECORDS = [
    {
        "alarm_id": "core-101",
        "link_id": "HNI-SGN-CORE-01",
        "site_id": "HNI001",
        "device": "HNI-CORE-R01",
        "interface": "xe-0/0/1",
        "severity": "critical",
        "service": "mobile-data",
        "latency_ms": 145,
        "packet_loss_pct": 4.1,
        "throughput_mbps": 120,
        "timestamp": "2026-07-06T10:02:00Z",
    },
    {
        "alarm_id": "core-102",
        "link_id": "HNI-SGN-CORE-01",
        "site_id": "SGN002",
        "device": "SGN-CORE-R02",
        "interface": "xe-1/0/2",
        "severity": "major",
        "service": "voice",
        "latency_ms": 132,
        "packet_loss_pct": 2.8,
        "throughput_mbps": 95,
        "timestamp": "2026-07-06T10:03:00Z",
    },
    {
        "alarm_id": "core-103",
        "link_id": "DNG-HNI-BACKUP-02",
        "site_id": "DNG003",
        "device": "DNG-AGG-R03",
        "interface": "ge-0/0/5",
        "severity": "minor",
        "service": "enterprise-vpn",
        "latency_ms": 58,
        "packet_loss_pct": 0.4,
        "throughput_mbps": 340,
        "timestamp": "2026-07-06T10:04:00Z",
    },
]


SEVERITY_WEIGHT = {
    "critical": 50,
    "major": 30,
    "minor": 12,
    "warning": 8,
    "normal": 0,
}


def read_args():
    try:
        with open("args.json", "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def as_text(value, default="unknown"):
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def as_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def normalize_severity(value):
    severity = as_text(value, "normal").lower()
    if severity in {"crit", "critical", "p1"}:
        return "critical"
    if severity in {"maj", "major", "p2"}:
        return "major"
    if severity in {"min", "minor", "p3"}:
        return "minor"
    if severity in {"warn", "warning"}:
        return "warning"
    return "normal"


def group_records(records):
    groups = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        link_id = as_text(record.get("link_id"))
        key = link_id
        if key not in groups:
            groups[key] = {
                "link_id": link_id,
                "sites": set(),
                "devices": set(),
                "interfaces": set(),
                "services": set(),
                "alarm_ids": [],
                "severity_counts": {"critical": 0, "major": 0, "minor": 0, "warning": 0, "normal": 0},
                "max_latency_ms": 0.0,
                "max_packet_loss_pct": 0.0,
                "min_throughput_mbps": None,
            }
        group = groups[key]
        group["sites"].add(as_text(record.get("site_id")))
        group["devices"].add(as_text(record.get("device")))
        group["interfaces"].add(as_text(record.get("interface")))
        group["services"].add(as_text(record.get("service")))
        group["alarm_ids"].append(as_text(record.get("alarm_id"), "no-alarm-id"))
        severity = normalize_severity(record.get("severity"))
        group["severity_counts"][severity] += 1
        group["max_latency_ms"] = max(group["max_latency_ms"], as_number(record.get("latency_ms")))
        group["max_packet_loss_pct"] = max(
            group["max_packet_loss_pct"],
            as_number(record.get("packet_loss_pct")),
        )
        throughput = as_number(record.get("throughput_mbps"), 0.0)
        if throughput > 0:
            current = group["min_throughput_mbps"]
            group["min_throughput_mbps"] = throughput if current is None else min(current, throughput)
    return groups


def score_group(group):
    score = 0.0
    drivers = []
    for severity, count in group["severity_counts"].items():
        if count:
            score += SEVERITY_WEIGHT[severity] * count
            drivers.append(f"{count} {severity} alarm(s)")
    service_count = len(group["services"])
    site_count = len(group["sites"])
    if service_count > 1:
        score += service_count * 10
        drivers.append(f"{service_count} affected services")
    if site_count > 1:
        score += site_count * 12
        drivers.append(f"{site_count} impacted sites")
    if group["max_latency_ms"] >= 100:
        score += min((group["max_latency_ms"] - 100) / 2 + 15, 35)
        drivers.append(f"latency peak {group['max_latency_ms']:.0f} ms")
    if group["max_packet_loss_pct"] >= 2:
        score += min(group["max_packet_loss_pct"] * 8, 35)
        drivers.append(f"packet loss peak {group['max_packet_loss_pct']:.2f}%")
    if group["min_throughput_mbps"] is not None and group["min_throughput_mbps"] < 100:
        score += min((100 - group["min_throughput_mbps"]) / 4, 20)
        drivers.append(f"low throughput {group['min_throughput_mbps']:.1f} Mbps")
    return round(min(score, 100.0), 1), drivers


def impact_level(score):
    if score >= 80:
        return "high"
    if score >= 45:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def recommendation_for(group, level):
    recommendations = []
    if level == "high":
        recommendations.append("Open an incident bridge and validate core link redundancy immediately.")
    elif level == "medium":
        recommendations.append("Escalate to transport/core team if degradation persists beyond the monitoring window.")
    else:
        recommendations.append("Monitor and correlate with nearby site or service alarms.")
    if group["max_packet_loss_pct"] >= 2:
        recommendations.append("Check interface errors, optical levels, and packet drops on both link endpoints.")
    if group["max_latency_ms"] >= 100:
        recommendations.append("Inspect congestion, route changes, and queueing on the affected path.")
    return list(dict.fromkeys(recommendations))


def summarize(records, impacts):
    severity_counts = {"critical": 0, "major": 0, "minor": 0, "warning": 0, "normal": 0}
    sites = set()
    links = set()
    for record in records:
        if isinstance(record, dict):
            severity_counts[normalize_severity(record.get("severity"))] += 1
            sites.add(as_text(record.get("site_id")))
            links.add(as_text(record.get("link_id")))
    return {
        "total_records": len([record for record in records if isinstance(record, dict)]),
        "impacted_links": len(links),
        "impacted_sites": len(sites),
        "severity_counts": severity_counts,
        "top_link": impacts[0]["link_id"] if impacts else None,
    }


def main():
    args = read_args()
    records = args.get("records")
    if not isinstance(records, list) or not records:
        records = DEFAULT_RECORDS

    impacts = []
    for group in group_records(records).values():
        score, drivers = score_group(group)
        level = impact_level(score)
        impacts.append(
            {
                "link_id": group["link_id"],
                "impact_score": score,
                "impact_level": level,
                "sites": sorted(group["sites"]),
                "devices": sorted(group["devices"]),
                "interfaces": sorted(group["interfaces"]),
                "services": sorted(group["services"]),
                "alarm_ids": group["alarm_ids"],
                "severity_counts": group["severity_counts"],
                "drivers": drivers,
                "recommendations": recommendation_for(group, level),
            }
        )
    impacts.sort(key=lambda item: item["impact_score"], reverse=True)
    output = {
        "summary": summarize(records, impacts),
        "impacts": impacts,
        "recommendations": [
            "Start with the highest impact_score link.",
            "Mention affected services and impacted sites in the escalation note.",
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
