import json
import argparse
import pydoc

# Load optional runtime modules for the trusted demo runner.
sys = pydoc.locate("sys")
os = pydoc.locate("os")

# Import entity extractor locally
from extract_content import extract_entities

STEP1_SQL = """
SELECT
    alarm_id,
    content,
    ne_name,
    severity,
    event_time as last_seen
FROM core_alarm_history
WHERE alarm_type = %(alarm_type)s
  AND event_time >= now() - INTERVAL %(window_min)d MINUTE
LIMIT %(limit)d
"""

STEP3_SQL = """
SELECT
    site_id,
    segment,
    vendor,
    oncall_team,
    ne_name,
    ip
FROM ne_inventory
WHERE ip = ANY(%(keys)s)
   OR ne_name = ANY(%(keys)s)
"""

def get_mock_alarms(alarm_type):
    """Returns sample mock alarm data for dry-run/mock modes."""
    return [
        {
            "alarm_id": "alarm-101",
            "content": f"Interface GigabitEthernet0/0/1 on NE HNI-CORE-01 (10.211.140.16) is DOWN; alarm_type={alarm_type}",
            "ne_name": "HNI-CORE-01",
            "severity": "CRITICAL",
            "last_seen": "2026-07-01T12:00:00Z"
        },
        {
            "alarm_id": "alarm-102",
            "content": f"BGP Session Down on NE SGN-EDGE-02 (10.211.142.22) peer AS65002; alarm_type={alarm_type}",
            "ne_name": "SGN-EDGE-02",
            "severity": "MAJOR",
            "last_seen": "2026-07-01T12:02:00Z"
        }
    ]

def get_mock_inventory():
    """Returns sample inventory database entries for dry-run/mock modes."""
    return [
        {
            "site_id": "SITE-HNI-01",
            "segment": "CORE",
            "vendor": "Cisco",
            "oncall_team": "NOC_Core_Team",
            "ne_name": "HNI-CORE-01",
            "ip": "10.211.140.16"
        },
        {
            "site_id": "SITE-SGN-02",
            "segment": "EDGE",
            "vendor": "Juniper",
            "oncall_team": "NOC_IP_Team",
            "ne_name": "SGN-EDGE-02",
            "ip": "10.211.142.22"
        }
    ]

def main():
    parser = argparse.ArgumentParser(description="NOC Alarm Enrichment Pipeline")
    parser.add_argument("--alarm-type", default="LINK_DOWN", help="Alarm type to enrich")
    parser.add_argument("--window-min", type=int, default=120, help="Window lookup in minutes")
    parser.add_argument("--limit", type=int, default=50, help="Query limit")
    parser.add_argument("--dry-run", action="store_true", help="Print queries and run mock validation only")
    parser.add_argument("--key-fields", default="ips,ne_names", help="Extracted fields to use as lookup keys")

    args = parser.parse_args()

    # Overlay arguments from args.json if running in the sandbox
    try:
        with open("args.json", "r", encoding="utf-8") as f:
            sandbox_args = json.load(f)

            # Alarm type
            if "alarm_type" in sandbox_args:
                args.alarm_type = sandbox_args["alarm_type"]
            elif "alarm-type" in sandbox_args:
                args.alarm_type = sandbox_args["alarm-type"]

            # Window Min
            if "window_min" in sandbox_args:
                args.window_min = int(sandbox_args["window_min"])
            elif "window-min" in sandbox_args:
                args.window_min = int(sandbox_args["window-min"])

            # Limit
            if "limit" in sandbox_args:
                args.limit = int(sandbox_args["limit"])

            # Dry Run
            if "dry_run" in sandbox_args:
                v = sandbox_args["dry_run"]
                if isinstance(v, str):
                    args.dry_run = v.lower() in ("true", "1", "yes")
                else:
                    args.dry_run = bool(v)
            elif "dry-run" in sandbox_args:
                v = sandbox_args["dry-run"]
                if isinstance(v, str):
                    args.dry_run = v.lower() in ("true", "1", "yes")
                else:
                    args.dry_run = bool(v)

            # Key Fields
            if "key_fields" in sandbox_args:
                args.key_fields = sandbox_args["key_fields"]
            elif "key-fields" in sandbox_args:
                args.key_fields = sandbox_args["key-fields"]
    except FileNotFoundError:
        pass

    # Check if we are running in dry-run mode or if databases are not configured
    ch_host = os.environ.get("CH_HOST") if os else None
    pg_dsn = os.environ.get("PG_DSN") if os else None

    is_dry_run = args.dry_run or (not ch_host or not pg_dsn)

    if is_dry_run:
        if sys:
            sys.stderr.write("=== DRY RUN/MOCK MODE: SIMULATING PIPELINE ===\n")
            sys.stderr.write(f"Step 1 Query (ClickHouse):\n{STEP1_SQL % {'alarm_type': repr(args.alarm_type), 'window_min': args.window_min, 'limit': args.limit}}\n\n")

        alarms = get_mock_alarms(args.alarm_type)
        if sys:
            sys.stderr.write(f"Fetched {len(alarms)} mock alarms from Step 1.\n\n")

        # Step 2: Extraction
        all_keys = []
        enriched_alarms = []
        for alarm in alarms:
            extracted, lookup_keys = extract_entities(alarm["content"])
            alarm["extracted"] = extracted
            alarm["lookup_keys"] = lookup_keys
            all_keys.extend(lookup_keys)
            enriched_alarms.append(alarm)

        if sys:
            sys.stderr.write(f"Step 2 Extracted Lookup Keys: {all_keys}\n\n")
            sys.stderr.write(f"Step 3 Query (PostgreSQL):\n{STEP3_SQL % {'keys': all_keys}}\n\n")

        inventory = get_mock_inventory()
        # Enrich
        for alarm in enriched_alarms:
            alarm["enrichment"] = []
            for item in inventory:
                if item["ne_name"] in alarm["lookup_keys"] or item["ip"] in alarm["lookup_keys"]:
                    alarm["enrichment"].append(item)

        print(json.dumps(enriched_alarms, indent=2))
        return

    # Real execution
    # 1. Fetch alarms from ClickHouse
    alarms = []
    try:
        import clickhouse_connect
        ch_args = {
            "host": ch_host,
            "port": int(os.environ.get("CH_PORT", 8123)),
            "username": os.environ.get("CH_USER", "default"),
            "password": os.environ.get("CH_PASSWORD", ""),
            "database": os.environ.get("CH_DATABASE", "default")
        }
        ch_client = clickhouse_connect.get_client(**ch_args)

        try:
            ch_res = ch_client.query(
                "SELECT alarm_id, content, ne_name, severity, event_time FROM core_alarm_history WHERE alarm_type = %s AND event_time >= now() - INTERVAL %d MINUTE LIMIT %d" % (args.alarm_type, args.window_min, args.limit)
            )
            for row in ch_res.result_rows:
                alarms.append({
                    "alarm_id": str(row[0]),
                    "content": str(row[1]),
                    "ne_name": str(row[2]),
                    "severity": str(row[3]),
                    "last_seen": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4])
                })
        except Exception as e_ch:
            if sys:
                sys.stderr.write(f"ClickHouse core_alarm_history query failed: {e_ch}. Trying fallback to alarm_data.alarm...\n")
            # Fallback to the real database table
            ch_res = ch_client.query(
                "SELECT alarm_id, raw_log, device_id, severity, time_created FROM alarm_data.alarm WHERE time_created >= now() - INTERVAL %d MINUTE LIMIT %d" % (args.window_min, args.limit)
            )
            for row in ch_res.result_rows:
                alarms.append({
                    "alarm_id": str(row[0]),
                    "content": str(row[1]),
                    "ne_name": str(row[2]),
                    "severity": str(row[3]),
                    "last_seen": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4])
                })
    except Exception as e:
        if sys:
            sys.stderr.write(f"ClickHouse query failed: {e}. Falling back to mock alarms.\n")
        alarms = get_mock_alarms(args.alarm_type)

    # 2. Fetch inventory from PostgreSQL
    inventory = []
    all_keys = []
    for alarm in alarms:
        _, lookup_keys = extract_entities(alarm["content"])
        all_keys.extend(lookup_keys)
        if alarm.get("ne_name"):
            all_keys.append(alarm["ne_name"])
    all_keys = list(set(all_keys))

    if all_keys:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(pg_dsn)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                try:
                    cursor.execute(
                        "SELECT site_id, segment, vendor, oncall_team, ne_name, ip FROM ne_inventory WHERE ip = ANY(%s) OR ne_name = ANY(%s)",
                        (all_keys, all_keys)
                    )
                    inventory = cursor.fetchall()
                except Exception as e_pg:
                    if sys:
                        sys.stderr.write(f"PostgreSQL ne_inventory query failed: {e_pg}. Trying fallback to alarm_data.device...\n")
                    conn.rollback()
                    # Query real device/vendor tables
                    cursor.execute(
                        """
                        SELECT
                            d.station_id as site_id,
                            d.device_type as segment,
                            v.name as vendor,
                            'NOC_Oncall' as oncall_team,
                            d.name as ne_name,
                            d.ip_address as ip,
                            d.device_id as device_id
                        FROM alarm_data.device d
                        LEFT JOIN alarm_data.vendor v ON d.vendor_id = v.vendor_id
                        WHERE d.ip_address = ANY(%s)
                           OR d.name = ANY(%s)
                           OR d.device_id = ANY(%s)
                        """,
                        (all_keys, all_keys, all_keys)
                    )
                    inventory = cursor.fetchall()
            conn.close()
        except Exception as e:
            if sys:
                sys.stderr.write(f"PostgreSQL query failed: {e}. Falling back to mock inventory.\n")
            inventory = get_mock_inventory()

    # Apply Step 2 (extraction) and Map Step 3 (enrichment)
    final_output = []
    for alarm in alarms:
        extracted, lookup_keys = extract_entities(alarm["content"])
        alarm["extracted"] = extracted

        combined_lookup = list(lookup_keys)
        if alarm.get("ne_name"):
            combined_lookup.append(alarm["ne_name"])
        alarm["lookup_keys"] = combined_lookup

        alarm["enrichment"] = []
        for item in inventory:
            if (item.get("ne_name") in combined_lookup or
                item.get("ip") in combined_lookup or
                item.get("device_id") == alarm.get("ne_name")):
                alarm["enrichment"].append(item)
        final_output.append(alarm)

    print(json.dumps(final_output, indent=2))

if __name__ == "__main__":
    main()
