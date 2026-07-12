from __future__ import annotations

import io
import unittest
import unittest.mock
import zipfile

from sqlalchemy.exc import IntegrityError


class FakeLLMResponse:
    content = (
        '{"domain_score": 0.95, "reason": "telecom NOC workflow", "suspicious_points": "None"}'
    )


class FakeLLMGateway:
    providers = ("fake",)

    async def invoke(self, *args, **kwargs):
        return FakeLLMResponse()


class FakeSkill:
    def __init__(self):
        self.id = "skill-1"
        self.name = None
        self.description = None
        self.skill_md = None
        self.frontmatter = None
        self.bundled_files = None
        self.script_manifest = None
        self.status = None
        self.is_malicious = False
        self.security_review_log = None


class FakeSkillRepository:
    def __init__(self):
        self.created: list[FakeSkill] = []
        self.updated: list[FakeSkill] = []

    def get_skill_by_name(self, db, name: str):
        for s in self.created:
            if s.name == name:
                return s
        return None

    def create_uploaded_skill(
        self,
        db,
        *,
        name: str,
        description: str,
        skill_md: str,
        frontmatter: dict | None = None,
        bundled_files: dict | None = None,
        script_manifest: dict | None = None,
    ):
        skill = FakeSkill()
        skill.name = name
        skill.description = description
        skill.skill_md = skill_md
        skill.frontmatter = frontmatter or {}
        skill.bundled_files = bundled_files or {}
        skill.script_manifest = script_manifest or {}
        skill.status = "uploaded"
        self.created.append(skill)
        return skill

    def update_sandbox_result(
        self,
        db,
        *,
        skill_id,
        status: str,
        review_log: str,
        is_malicious: bool,
    ):
        skill = self.created[-1]
        skill.status = status
        skill.security_review_log = review_log
        skill.is_malicious = is_malicious
        self.updated.append(skill)
        return skill


class SkillUploadServiceTests(unittest.IsolatedAsyncioTestCase):
    def _create_zip(self, skill_md: str, extra_files: dict[str, str] = None) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("SKILL.md", skill_md)
            if extra_files:
                for k, v in extra_files.items():
                    z.writestr(k, v)
        return buf.getvalue()

    async def test_valid_skill_is_persisted_for_human_review_with_metadata(self) -> None:
        from app.services.skills import SkillUploadCommand, SkillValidationService

        repository = FakeSkillRepository()
        service = SkillValidationService(skill_repository=repository)

        skill_md = """---
name: collect-node-alarm
description: Collect alarm status from telecom node for NOC troubleshooting. alarm alert kpi
metadata:
  version: "1.0.0"
---
# Collect node alarm
Instructions here.
"""
        zip_bytes = self._create_zip(
            skill_md,
            {"scripts/helper.py": "def run():\n    return 1\n"},
        )

        # Ép sandbox không khả dụng để kiểm thử nhánh pending_sandbox một cách tất định,
        # không phụ thuộc việc máy chạy test có Docker hay không.
        with unittest.mock.patch(
            "app.sandbox.docker_executor.build_sandbox_executor_from_settings",
            return_value=None,
        ):
            result = await service.upload_skill(
                db=object(),
                llm_gateway=FakeLLMGateway(),
                command=SkillUploadCommand(zip_bytes=zip_bytes),
            )

        self.assertEqual(result.status, "PENDING_REVIEW")
        self.assertEqual(repository.created[0].name, "collect-node-alarm")
        self.assertEqual(
            repository.created[0].bundled_files["scripts/helper.py"]["encoding"],
            "utf-8",
        )
        manifest = repository.created[0].script_manifest
        self.assertIn("scripts/helper.py", manifest)
        self.assertEqual("passed", manifest["scripts/helper.py"]["status"])
        self.assertIn("sha256:", manifest["scripts/helper.py"]["script_hash"])
        self.assertEqual(
            {"type": "object", "additionalProperties": True},
            manifest["scripts/helper.py"]["input_schema"],
        )
        self.assertNotIn("max_output_chars", manifest["scripts/helper.py"]["limits"])
        self.assertEqual(repository.updated[0].status, "testing")

    async def test_sandbox_script_failure_rejects_package_when_sandbox_is_available(self) -> None:
        from app.sandbox.docker_executor import SandboxExecutionResult
        from app.services.skills import (
            SkillUploadCommand,
            SkillValidationError,
            SkillValidationService,
        )

        class FailingSandboxExecutor:
            async def validate_skill_script(self, *args, **kwargs):
                return SandboxExecutionResult(
                    stdout="",
                    stderr="boom",
                    exit_code=1,
                )

        repository = FakeSkillRepository()
        service = SkillValidationService(skill_repository=repository)
        skill_md = """---
name: check-kpis
description: Check telecom KPI alarms for NOC troubleshooting. alarm alert kpi
---
# Check KPIs
Run `scripts/check.py`.
"""

        with unittest.mock.patch(
            "app.sandbox.docker_executor.build_sandbox_executor_from_settings",
            return_value=FailingSandboxExecutor(),
        ):
            with self.assertRaises(SkillValidationError) as ctx:
                await service.upload_skill(
                    db=object(),
                    llm_gateway=FakeLLMGateway(),
                    command=SkillUploadCommand(
                        zip_bytes=self._create_zip(
                            skill_md,
                            {"scripts/check.py": "print('ok')\n"},
                        )
                    ),
                )

        self.assertEqual("REJECTED", ctx.exception.status)
        self.assertIn("sandbox validation", ctx.exception.message)
        self.assertEqual(repository.updated[0].status, "rejected")

    async def test_run_spec_keeps_args_json_when_llm_proposes_unsupported_mode(self) -> None:
        from app.services.skills import SkillValidationService

        service = SkillValidationService(skill_repository=FakeSkillRepository())
        package = service.parse_package(
            self._create_zip(
                """---
name: check-kpis
description: Check telecom KPI alarms for NOC troubleshooting.
---
# Check KPIs
Run the approved script.
""",
                {"scripts/check.py": "print('ok')\n"},
            )
        )
        proposal = {
            "scripts/check.py": {
                "runtime": {"arguments_mode": "none"},
            }
        }

        with unittest.mock.patch.object(
            service,
            "_invoke_llm_run_spec_analyzer",
            new=unittest.mock.AsyncMock(return_value=proposal),
        ):
            manifest = await service._build_initial_script_manifest(object(), package, [])

        self.assertEqual("args_json", manifest["scripts/check.py"]["runtime"]["arguments_mode"])

    async def test_malicious_skill_is_rejected_and_recorded_once(self) -> None:
        from app.services.skills import (
            SkillUploadCommand,
            SkillValidationError,
            SkillValidationService,
        )

        repository = FakeSkillRepository()
        service = SkillValidationService(skill_repository=repository)

        skill_md = """---
name: steal-env
description: Bad skill. alarm alert kpi
---
# Steal env
Instructions.
"""
        zip_bytes = self._create_zip(
            skill_md,
            {"scripts/bad.py": "import os\ndef run():\n    eval('1')\n"},
        )

        with self.assertRaises(SkillValidationError) as ctx:
            await service.upload_skill(
                db=object(),
                llm_gateway=FakeLLMGateway(),
                command=SkillUploadCommand(zip_bytes=zip_bytes),
            )

        self.assertEqual(ctx.exception.status, "REJECTED")
        self.assertEqual(len(repository.created), 1)
        self.assertEqual(repository.updated[0].status, "rejected")
        self.assertTrue(repository.updated[0].is_malicious)

    async def test_nested_skill_folder_normalizes_resource_paths(self) -> None:
        from app.services.skills import SkillUploadCommand, SkillValidationService

        repository = FakeSkillRepository()
        service = SkillValidationService(skill_repository=repository)
        skill_md = """---
name: check-kpis
description: Check telecom node KPI alarms and latency during NOC troubleshooting.
---
# Check KPIs
Read [the checklist](references/checklist.md).
"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("check-kpis/SKILL.md", skill_md)
            archive.writestr("check-kpis/references/checklist.md", "Check latency first.")

        await service.upload_skill(
            db=object(),
            llm_gateway=FakeLLMGateway(),
            command=SkillUploadCommand(zip_bytes=buf.getvalue()),
        )

        self.assertIn("references/checklist.md", repository.created[0].bundled_files)
        self.assertNotIn("check-kpis/references/checklist.md", repository.created[0].bundled_files)

    async def test_rejects_non_standard_skill_name(self) -> None:
        from app.services.skills import (
            SkillUploadCommand,
            SkillValidationError,
            SkillValidationService,
        )

        skill_md = """---
name: check_kpis
description: Check telecom KPI alarms for NOC troubleshooting.
---
# Check KPIs
Instructions.
"""

        with self.assertRaises(SkillValidationError):
            await SkillValidationService(skill_repository=FakeSkillRepository()).upload_skill(
                db=object(),
                llm_gateway=FakeLLMGateway(),
                command=SkillUploadCommand(zip_bytes=self._create_zip(skill_md)),
            )

    async def test_duplicate_name_does_not_delete_existing_skill(self) -> None:
        from app.services.skills import (
            SkillUploadCommand,
            SkillValidationError,
            SkillValidationService,
        )

        repository = FakeSkillRepository()
        existing = FakeSkill()
        existing.name = "check-kpis"
        existing.status = "ready"
        repository.created.append(existing)
        skill_md = """---
name: check-kpis
description: Check telecom KPI alarms for NOC troubleshooting.
---
# Check KPIs
Instructions.
"""

        with self.assertRaises(SkillValidationError) as ctx:
            await SkillValidationService(skill_repository=repository).upload_skill(
                db=object(),
                llm_gateway=FakeLLMGateway(),
                command=SkillUploadCommand(zip_bytes=self._create_zip(skill_md)),
            )

        self.assertEqual(409, ctx.exception.http_status_code)
        self.assertEqual([existing], repository.created)

    async def test_concurrent_duplicate_insert_returns_conflict(self) -> None:
        from app.services.skills import (
            SkillUploadCommand,
            SkillValidationError,
            SkillValidationService,
        )

        class RacingRepository(FakeSkillRepository):
            def create_uploaded_skill(self, *args, **kwargs):
                raise IntegrityError("INSERT skills", {}, RuntimeError("unique violation"))

        class RollbackDb:
            rolled_back = False

            def rollback(self):
                self.rolled_back = True

        skill_md = """---
name: check-kpis
description: Check telecom KPI alarms for NOC troubleshooting.
---
# Check KPIs
Instructions.
"""
        db = RollbackDb()

        with self.assertRaises(SkillValidationError) as ctx:
            await SkillValidationService(skill_repository=RacingRepository()).upload_skill(
                db=db,
                llm_gateway=FakeLLMGateway(),
                command=SkillUploadCommand(zip_bytes=self._create_zip(skill_md)),
            )

        self.assertEqual(409, ctx.exception.http_status_code)
        self.assertTrue(db.rolled_back)

    async def test_rejects_archive_larger_than_configured_limit(self) -> None:
        from app.services.skills import (
            SkillUploadCommand,
            SkillValidationError,
            SkillValidationService,
        )

        service = SkillValidationService(skill_repository=FakeSkillRepository())
        service.MAX_ARCHIVE_BYTES = 8

        with self.assertRaises(SkillValidationError) as ctx:
            await service.upload_skill(
                db=object(),
                llm_gateway=FakeLLMGateway(),
                command=SkillUploadCommand(zip_bytes=b"not-a-small-zip"),
            )

        self.assertIn("kích thước", ctx.exception.message)


class SkillPackageParsingTests(unittest.TestCase):
    @staticmethod
    def _zip(entries: dict[str, str | bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            for path, content in entries.items():
                archive.writestr(path, content)
        return buf.getvalue()

    @staticmethod
    def _skill_md(name: str = "check-kpis") -> str:
        return f"""---
name: {name}
description: Check telecom KPI alarms and latency during NOC troubleshooting.
metadata:
  version: "2.0"
---
# Check KPIs
Follow the NOC checklist.
"""

    def test_preserves_binary_assets_as_base64(self) -> None:
        from app.services.skills import SkillValidationService

        package = SkillValidationService().parse_package(
            self._zip(
                {
                    "check-kpis/SKILL.md": self._skill_md(),
                    "check-kpis/assets/diagram.png": b"\x89PNG\r\n\x1a\n\x00\xff",
                }
            )
        )

        asset = package.bundled_files["assets/diagram.png"]
        self.assertEqual("base64", asset["encoding"])
        self.assertEqual("image/png", asset["media_type"])

    def test_requires_exact_skill_md_filename(self) -> None:
        from app.services.skills import SkillValidationError, SkillValidationService

        with self.assertRaises(SkillValidationError):
            SkillValidationService().parse_package(
                self._zip({"check-kpis/evilSKILL.md": self._skill_md()})
            )

    def test_allows_folder_name_to_differ_from_skill_name(self) -> None:
        from app.services.skills import SkillValidationService

        package = SkillValidationService().parse_package(
            self._zip(
                {
                    "NoAlarmEnrichment/SKILL.md": self._skill_md("check-kpis"),
                    "NoAlarmEnrichment/scripts/check.py": "def run():\n    return 'ok'\n",
                }
            )
        )

        self.assertEqual("check-kpis", package.name)
        self.assertIn("scripts/check.py", package.bundled_files)

    def test_rejects_non_mapping_frontmatter(self) -> None:
        from app.services.skills import SkillValidationError, SkillValidationService

        skill_md = "---\n- name\n- description\n---\n# Instructions"
        with self.assertRaises(SkillValidationError):
            SkillValidationService().parse_package(self._zip({"SKILL.md": skill_md}))

    def test_rejects_non_spec_top_level_version(self) -> None:
        from app.services.skills import SkillValidationError, SkillValidationService

        skill_md = self._skill_md().replace(
            'metadata:\n  version: "2.0"',
            'version: "2.0"',
        )
        with self.assertRaises(SkillValidationError):
            SkillValidationService().parse_package(self._zip({"SKILL.md": skill_md}))

    def test_rejects_non_string_frontmatter_keys_as_validation_error(self) -> None:
        from app.services.skills import SkillValidationError, SkillValidationService

        skill_md = """---
name: check-kpis
description: Check telecom KPI alarms during NOC troubleshooting.
1: invalid
custom-field: invalid
---
# Check KPIs
Instructions.
"""
        with self.assertRaises(SkillValidationError):
            SkillValidationService().parse_package(self._zip({"SKILL.md": skill_md}))

    def test_rejects_dot_segment_in_archive_path(self) -> None:
        from app.services.skills import SkillValidationError, SkillValidationService

        with self.assertRaises(SkillValidationError):
            SkillValidationService().parse_package(
                self._zip(
                    {
                        "check-kpis/SKILL.md": self._skill_md(),
                        "check-kpis/scripts/./check.py": "print('ok')\n",
                    }
                )
            )


class SkillRepositoryReviewTests(unittest.TestCase):
    def test_human_rejection_appends_to_automated_review_log(self) -> None:
        from app.database.repositories.skills import SkillRepository

        skill = FakeSkill()
        skill.security_review_log = "[STATIC] Passed package scan."

        class Db:
            def get(self, model, skill_id):
                return skill

            def commit(self):
                pass

            def refresh(self, value):
                pass

        SkillRepository.reject_skill(Db(), "skill-1", review_log="Rejected: unclear instructions")

        self.assertIn("[STATIC] Passed package scan.", skill.security_review_log)
        self.assertIn("Rejected: unclear instructions", skill.security_review_log)


if __name__ == "__main__":
    unittest.main()
