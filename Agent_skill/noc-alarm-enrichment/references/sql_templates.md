# SQL Templates for NOC Alarm Enrichment

This document provides reference templates for Step 1 (ClickHouse) and Step 3 (PostgreSQL) queries used in the enrichment pipeline.

## 1. ClickHouse Alarm Query (Step 1)
ClickHouse stores the high-frequency historical log logs.
```sql
SELECT
    alarm_id,
    content,
    ne_name,
    severity,
    event_time
FROM core_alarm_history
WHERE alarm_type = %(alarm_type)s
  AND event_time >= now() - INTERVAL %(window_min)d MINUTE
LIMIT %(limit)d
```

### Table Schema expectations:
- `alarm_id`: String / UUID
- `content`: String containing raw log text
- `ne_name`: String matching network element hostnames (e.g. `REGION-ROLE-ID`)
- `severity`: Enum/String (CRITICAL, MAJOR, MINOR, WARNING)
- `event_time`: DateTime

---

## 2. PostgreSQL Inventory Query (Step 3)
PostgreSQL handles inventory asset mapping.
```sql
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
```

### Spring/Java Parameter Binding (Ref):
If porting this logic into a Spring Boot service, avoid concatenating arrays. Bind list variables to `text[]` arrays:
```java
String sql = "SELECT * FROM ne_inventory WHERE ip = ANY(?) OR ne_name = ANY(?)";
String[] keysArray = keysList.toArray(new String[0]);
java.sql.Array sqlArray = connection.createArrayOf("text", keysArray);
preparedStatement.setArray(1, sqlArray);
preparedStatement.setArray(2, sqlArray);
```
