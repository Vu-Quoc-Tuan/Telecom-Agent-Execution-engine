import os
import unittest

from app.config import settings
from app.connectors.clickhouse import TelcoClickHouseConnector
from app.connectors.postgres import TelcoPostgresConnector
from app.connectors.ssh import TelcoSSHConnector


@unittest.skipUnless(
    os.getenv("RUN_EXTERNAL_CONNECTOR_TESTS") == "1",
    "External connector integration tests require RUN_EXTERNAL_CONNECTOR_TESTS=1",
)
class ConnectorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ssh_connector_integration(self):
        if not settings.SSH_HOST:
            self.skipTest("SSH_HOST is not configured")

        connector = TelcoSSHConnector(
            host=settings.SSH_HOST,
            port=settings.SSH_PORT,
            username=settings.SSH_USER,
            password=settings.SSH_PASSWORD,
            timeout=10,
            auto_add_host_keys=True,
        )
        try:
            # We run a simple whoami command
            stdout, stderr = await connector.execute_command("whoami")
            self.assertTrue(stdout or stderr)
            self.assertIn(settings.SSH_USER, stdout.strip().lower())
        finally:
            connector.close()

    async def test_clickhouse_connector_integration(self):
        if not settings.CLICKHOUSE_HOST:
            self.skipTest("CLICKHOUSE_HOST is not configured")

        connector = TelcoClickHouseConnector(
            host=settings.CLICKHOUSE_HOST,
            port=settings.CLICKHOUSE_PORT,
            username=settings.CLICKHOUSE_USER,
            password=settings.CLICKHOUSE_PASSWORD,
            database=settings.CLICKHOUSE_DATABASE,
        )
        try:
            # Run a simple query
            rows = await connector.query("SELECT 1 AS test_val")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["test_val"], 1)
        finally:
            connector.close()

    async def test_postgres_connector_integration(self):
        if not settings.EXTERNAL_POSTGRES_HOST:
            self.skipTest("EXTERNAL_POSTGRES_HOST is not configured")

        connector = TelcoPostgresConnector(
            host=settings.EXTERNAL_POSTGRES_HOST,
            port=settings.EXTERNAL_POSTGRES_PORT,
            username=settings.EXTERNAL_POSTGRES_USER,
            password=settings.EXTERNAL_POSTGRES_PASSWORD,
            database=settings.EXTERNAL_POSTGRES_DATABASE,
        )
        try:
            # Run a simple query
            rows = await connector.query("SELECT 1 AS test_val")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["test_val"], 1)
        finally:
            connector.close()


if __name__ == "__main__":
    unittest.main()
