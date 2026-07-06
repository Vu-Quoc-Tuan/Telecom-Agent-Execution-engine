import json


DEFAULT_RECORDS = [
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
        "alarm_count": 5,
    },
    {
        "cell_id": "HNI-NR-014-C",
        "site_id": "HNI014",
        "node": "HNI-gNB-014",
        "availability_pct": 99.1,
        "drop_rate_pct": 1.1,
        "latency_ms": 42,
        "packet_loss_pct": 0.3,
        "throughput_mbps": 88,
        "affected_subscribers": 120,
        "alarm_count": 1,
    },
]


DEFAULT_THRESHOLDS = {
    "availability_critical": 97.0,
    "drop_rate_major": 3.0,
    "latency_major": 100.0,
    "packet_loss_major": 2.0,
    "throughput_low": 25.0,
    "affected_subscribers_major": 500,
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


def number(record, key, default=0.0):
    value = record.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def text(record, key, default="unknown"):
    value = record.get(key, default)
    if value is None:
        return default
    return str(value)


def classify(score):
    if score >= 80:
        return "critical"
    if score >= 50:
        return "major"
    if score >= 25:
        return "minor"
    return "normal"


def analyze_record(record, thresholds):
    score = 0.0
    drivers = []
    actions = []

    availability = number(record, "availability_pct", 100.0)
    if availability < thresholds["availability_critical"]:
        score += min((thresholds["availability_critical"] - availability) * 8, 35)
        drivers.append(f"availability below target: {availability:.2f}%")
        actions.append("Check RAN node reachability, sector availability, and recent maintenance events.")

    drop_rate = number(record, "drop_rate_pct")
    if drop_rate >= thresholds["drop_rate_major"]:
        score += min(drop_rate * 7, 30)
        drivers.append(f"drop rate elevated: {drop_rate:.2f}%")
        actions.append("Inspect radio interference, handover failures, and cell admission/congestion counters.")

    latency = number(record, "latency_ms")
    if latency >= thresholds["latency_major"]:
        score += min((latency - thresholds["latency_major"]) / 3 + 18, 28)
        drivers.append(f"latency elevated: {latency:.0f} ms")
        actions.append("Check transport backhaul latency and upstream packet queueing.")

    packet_loss = number(record, "packet_loss_pct")
    if packet_loss >= thresholds["packet_loss_major"]:
        score += min(packet_loss * 9, 30)
        drivers.append(f"packet loss elevated: {packet_loss:.2f}%")
        actions.append("Validate backhaul interface errors and transmission path health.")

    throughput = number(record, "throughput_mbps")
    if throughput and throughput < thresholds["throughput_low"]:
        score += min((thresholds["throughput_low"] - throughput) * 1.8, 22)
        drivers.append(f"throughput low: {throughput:.1f} Mbps")
        actions.append("Compare PRB utilization, scheduler congestion, and user-plane throughput trend.")

    affected = number(record, "affected_subscribers")
    if affected >= thresholds["affected_subscribers_major"]:
        score += min(affected / 45, 25)
        drivers.append(f"affected subscribers: {int(affected)}")
        actions.append("Escalate with subscriber impact and affected site/cell identifiers.")

    alarm_count = number(record, "alarm_count")
    if alarm_count >= 3:
        score += min(alarm_count * 3, 18)
        drivers.append(f"co-occurring alarms: {int(alarm_count)}")

    if not drivers:
        drivers.append("no KPI breached the configured anomaly thresholds")
        actions.append("Keep monitoring and compare against baseline before escalation.")

    score = round(min(score, 100.0), 1)
    return {
        "cell_id": text(record, "cell_id"),
        "site_id": text(record, "site_id"),
        "node": text(record, "node"),
        "score": score,
        "severity": classify(score),
        "drivers": drivers,
        "recommended_actions": list(dict.fromkeys(actions)),
    }


def summarize(results):
    severity_counts = {"critical": 0, "major": 0, "minor": 0, "normal": 0}
    site_scores = {}
    for item in results:
        severity_counts[item["severity"]] += 1
        site = item["site_id"]
        site_scores[site] = site_scores.get(site, 0.0) + item["score"]
    top_site = None
    if site_scores:
        top_site = sorted(site_scores.items(), key=lambda pair: pair[1], reverse=True)[0][0]
    return {
        "total_cells": len(results),
        "severity_counts": severity_counts,
        "top_site_by_score": top_site,
    }


def main():
    args = read_args()
    records = args.get("records")
    if not isinstance(records, list) or not records:
        records = DEFAULT_RECORDS

    thresholds = dict(DEFAULT_THRESHOLDS)
    custom_thresholds = args.get("thresholds")
    if isinstance(custom_thresholds, dict):
        for key, value in custom_thresholds.items():
            if key in thresholds:
                thresholds[key] = number(custom_thresholds, key, thresholds[key])

    results = [analyze_record(record if isinstance(record, dict) else {}, thresholds) for record in records]
    results.sort(key=lambda item: item["score"], reverse=True)
    output = {
        "summary": summarize(results),
        "ranked_cells": results,
        "operator_notes": [
            "Treat critical cells as immediate NOC triage candidates.",
            "Use the drivers list to choose whether RAN, backhaul, or capacity teams should inspect first.",
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
