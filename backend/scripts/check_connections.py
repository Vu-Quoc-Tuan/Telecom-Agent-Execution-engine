from __future__ import annotations

import os
import sys
from pathlib import Path

import clickhouse_connect
import paramiko
import psycopg2
from dotenv import load_dotenv

repo_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(repo_root / ".env")
load_dotenv(repo_root / ".env.external")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def test_postgres() -> bool:
    print("\n1. CHECK POSTGRES")

    try:
        with psycopg2.connect(
            host=require_env("EXTERNAL_POSTGRES_HOST"),
            port=int(require_env("EXTERNAL_POSTGRES_PORT")),
            database=require_env("EXTERNAL_POSTGRES_DATABASE"),
            user=require_env("EXTERNAL_POSTGRES_USER"),
            password=require_env("EXTERNAL_POSTGRES_PASSWORD"),
            connect_timeout=10,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        current_database(),
                        current_user,
                        version()
                    """
                )
                metadata = cursor.fetchone()

                cursor.execute(
                    """
                    SELECT table_schema, table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema NOT IN (
                        'pg_catalog',
                        'information_schema'
                    )
                    ORDER BY table_schema, table_name
                    """
                )
                tables = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    ORDER BY schema_name
                    """
                )
                schemas = cursor.fetchall()

        print("Kết nối PostgreSQL thành công.")
        print(f"Metadata: {metadata}")
        print(f"Schemas: {schemas}")
        print(f"Tables: {tables}")
        return True

    except Exception as exc:
        print(f"Lỗi kết nối PostgreSQL: {type(exc).__name__}: {exc}")
        return False


def test_clickhouse() -> bool:
    print("\n2. CHECK CLICKHOUSE")

    client = None

    try:
        client = clickhouse_connect.get_client(
            host=require_env("CLICKHOUSE_HOST"),
            port=int(require_env("CLICKHOUSE_PORT")),
            database=require_env("CLICKHOUSE_DATABASE"),
            username=require_env("CLICKHOUSE_USER"),
            password=require_env("CLICKHOUSE_PASSWORD"),
            connect_timeout=10,
            send_receive_timeout=15,
        )

        metadata = client.query(
            """
            SELECT
                currentDatabase(),
                currentUser(),
                version()
            """
        ).result_rows

        databases = client.query("SHOW DATABASES").result_rows

        tables = client.query(
            """
            SELECT database, name, engine
            FROM system.tables
            WHERE database NOT IN (
                'system',
                'information_schema',
                'INFORMATION_SCHEMA'
            )
            ORDER BY database, name
            """
        ).result_rows

        print("Kết nối ClickHouse thành công.")
        print(f"Metadata: {metadata}")
        print(f"Databases: {databases}")
        print(f"Tables: {tables}")
        return True

    except Exception as exc:
        print(f"Lỗi kết nối ClickHouse: {type(exc).__name__}: {exc}")
        return False

    finally:
        if client is not None:
            client.close()


def test_ssh() -> bool:
    print("\n3. CHECK SSH")

    ssh = paramiko.SSHClient()

    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        ssh.connect(
            hostname=require_env("SSH_HOST"),
            port=int(require_env("SSH_PORT")),
            username=require_env("SSH_USER"),
            password=require_env("SSH_PASSWORD"),
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
        )

        command = """
        hostname
        whoami
        uname -a
        uptime
        df -h
        free -h
        """

        _, stdout, stderr = ssh.exec_command(command, timeout=15)

        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace")
        error_output = stderr.read().decode("utf-8", errors="replace")

        print("Kết nối SSH thành công.")
        print(f"Exit code: {exit_code}")
        print(output)

        if error_output.strip():
            print("STDERR:")
            print(error_output)
        return exit_code == 0

    except paramiko.ssh_exception.SSHException as exc:
        print(f"Lỗi SSH: {exc}")
        print(
            "Nếu đây là lần kết nối đầu tiên, hãy xác minh fingerprint và thêm host "
            "vào known_hosts trước khi chạy lại."
        )
        return False
    except Exception as exc:
        print(f"Lỗi kết nối SSH: {type(exc).__name__}: {exc}")
        return False
    finally:
        ssh.close()


if __name__ == "__main__":
    ok = test_postgres()
    ok = test_clickhouse() and ok
    ok = test_ssh() and ok
    sys.exit(0 if ok else 1)
