import argparse
import json
import os
from urllib.parse import quote_plus

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
    parser.add_argument("--ch-host", default="", help="ClickHouse host for real execution")
    parser.add_argument("--ch-port", type=int, default=0, help="ClickHouse HTTP port")
    parser.add_argument("--ch-user", default="", help="ClickHouse username")
    parser.add_argument("--ch-password", default="", help="ClickHouse password")
    parser.add_argument("--ch-database", default="", help="ClickHouse database")
    parser.add_argument("--pg-dsn", default="", help="PostgreSQL DSN for real execution")

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

            # Real execution connection settings
            if "ch_host" in sandbox_args:
                args.ch_host = sandbox_args["ch_host"]
            elif "ch-host" in sandbox_args:
                args.ch_host = sandbox_args["ch-host"]

            if "ch_port" in sandbox_args:
                args.ch_port = int(sandbox_args["ch_port"])
            elif "ch-port" in sandbox_args:
                args.ch_port = int(sandbox_args["ch-port"])

            if "ch_user" in sandbox_args:
                args.ch_user = sandbox_args["ch_user"]
            elif "ch-user" in sandbox_args:
                args.ch_user = sandbox_args["ch-user"]

            if "ch_password" in sandbox_args:
                args.ch_password = sandbox_args["ch_password"]
            elif "ch-password" in sandbox_args:
                args.ch_password = sandbox_args["ch-password"]

            if "ch_database" in sandbox_args:
                args.ch_database = sandbox_args["ch_database"]
            elif "ch-database" in sandbox_args:
                args.ch_database = sandbox_args["ch-database"]

            if "pg_dsn" in sandbox_args:
                args.pg_dsn = sandbox_args["pg_dsn"]
            elif "pg-dsn" in sandbox_args:
                args.pg_dsn = sandbox_args["pg-dsn"]
    except FileNotFoundError:
        pass

    ch_host = args.ch_host or os.environ.get("CH_HOST") or os.environ.get("CLICKHOUSE_HOST", "")
    ch_port = int(args.ch_port or os.environ.get("CH_PORT") or os.environ.get("CLICKHOUSE_PORT", 8123))
    ch_user = args.ch_user or os.environ.get("CH_USER") or os.environ.get("CLICKHOUSE_USER", "default")
    ch_password = args.ch_password or os.environ.get("CH_PASSWORD") or os.environ.get("CLICKHOUSE_PASSWORD", "")
    ch_database = args.ch_database or os.environ.get("CH_DATABASE") or os.environ.get("CLICKHOUSE_DATABASE", "default")
    pg_dsn = args.pg_dsn or os.environ.get("PG_DSN", "")
    if not pg_dsn and os.environ.get("EXTERNAL_POSTGRES_HOST"):
        pg_dsn = (
            "postgresql://"
            f"{quote_plus(os.environ.get('EXTERNAL_POSTGRES_USER', ''))}:"
            f"{quote_plus(os.environ.get('EXTERNAL_POSTGRES_PASSWORD', ''))}@"
            f"{os.environ.get('EXTERNAL_POSTGRES_HOST')}:"
            f"{os.environ.get('EXTERNAL_POSTGRES_PORT', 5432)}/"
            f"{os.environ.get('EXTERNAL_POSTGRES_DATABASE', '')}"
        )

    if args.dry_run:
        alarms = get_mock_alarms(args.alarm_type)

        # Step 2: Extraction
        all_keys = []
        enriched_alarms = []
        for alarm in alarms:
            extracted, lookup_keys = extract_entities(alarm["content"])
            alarm["extracted"] = extracted
            alarm["lookup_keys"] = lookup_keys
            all_keys.extend(lookup_keys)
            enriched_alarms.append(alarm)

        inventory = get_mock_inventory()
        # Enrich
        for alarm in enriched_alarms:
            alarm["enrichment"] = []
            for item in inventory:
                if item["ne_name"] in alarm["lookup_keys"] or item["ip"] in alarm["lookup_keys"]:
                    alarm["enrichment"].append(item)

        print(json.dumps(enriched_alarms, indent=2))
        return

    if not ch_host or not pg_dsn:
        parser.error(
            "database connection settings are required for real execution "
            "(ClickHouse host and PostgreSQL DSN)"
        )

    # Real execution
    # 1. Fetch alarms from ClickHouse
    alarms = []
    try:
        import clickhouse_connect

        ch_args = {
            "host": ch_host,
            "port": ch_port,
            "username": ch_user,
            "password": ch_password,
            "database": ch_database,
        }
        ch_client = clickhouse_connect.get_client(**ch_args)

        try:
            ch_res = ch_client.query(
                """
                SELECT alarm_id, content, ne_name, severity, event_time
                FROM core_alarm_history
                WHERE alarm_type = %(alarm_type)s
                  AND event_time >= now() - INTERVAL %(window_min)s MINUTE
                LIMIT %(limit)s
                """,
                parameters={
                    "alarm_type": args.alarm_type,
                    "window_min": args.window_min,
                    "limit": args.limit,
                },
            )
            for row in ch_res.result_rows:
                alarms.append(
                    {
                        "alarm_id": str(row[0]),
                        "content": str(row[1]),
                        "ne_name": str(row[2]),
                        "severity": str(row[3]),
                        "last_seen": row[4].isoformat()
                        if hasattr(row[4], "isoformat")
                        else str(row[4]),
                    }
                )
        except Exception:
            # Fallback to the real database table
            ch_res = ch_client.query(
                """
                SELECT alarm_id, raw_log, device_id, severity, time_created
                FROM alarm_data.alarm
                WHERE time_created >= now() - INTERVAL %(window_min)s MINUTE
                LIMIT %(limit)s
                """,
                parameters={"window_min": args.window_min, "limit": args.limit},
            )
            for row in ch_res.result_rows:
                alarms.append(
                    {
                        "alarm_id": str(row[0]),
                        "content": str(row[1]),
                        "ne_name": str(row[2]),
                        "severity": str(row[3]),
                        "last_seen": row[4].isoformat()
                        if hasattr(row[4], "isoformat")
                        else str(row[4]),
                    }
                )
    except Exception as exc:
        parser.exit(1, f"error: ClickHouse query failed: {exc}\n")

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
                        (all_keys, all_keys),
                    )
                    inventory = cursor.fetchall()
                except Exception:
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
                        (all_keys, all_keys, all_keys),
                    )
                    inventory = cursor.fetchall()
            conn.close()
        except Exception as exc:
            parser.exit(1, f"error: PostgreSQL query failed: {exc}\n")

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
            if (
                item.get("ne_name") in combined_lookup
                or item.get("ip") in combined_lookup
                or item.get("device_id") == alarm.get("ne_name")
            ):
                alarm["enrichment"].append(item)
        final_output.append(alarm)

    print(json.dumps(final_output, indent=2))

if __name__ == "__main__":
    main()
