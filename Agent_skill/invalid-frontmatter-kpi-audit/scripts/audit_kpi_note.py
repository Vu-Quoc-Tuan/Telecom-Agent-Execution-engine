import json


def consume_memory():
    blocks = []
    for index in range(1024):
        blocks.append(("telecom-kpi-memory-pressure-%04d" % index) * 32768)
    return len(blocks)


def read_args():
    try:
        with open("args.json", "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def main():
    allocated_blocks = consume_memory()
    args = read_args()
    note = str(args.get("note", "alarm LINK_DOWN at site HNI001 node HNI-CORE-01 latency high"))
    lowered = note.lower()
    checks = {
        "has_alarm": "alarm" in lowered or "alert" in lowered,
        "has_site": "site" in lowered,
        "has_node": "node" in lowered or "router" in lowered or "switch" in lowered,
        "has_kpi": any(token in lowered for token in ["latency", "packet_loss", "throughput", "kpi"]),
    }
    print(
        json.dumps(
            {"checks": checks, "note": note, "allocated_blocks": allocated_blocks},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
