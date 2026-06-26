from __future__ import annotations

import base64
import io
import mimetypes
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.safety import AgentSafetyGuard
from app.common.enums import SkillStatus
from app.database.repositories.skills import SkillRepository
from app.sandbox.domain_validator import TelecomDomainValidator
from app.sandbox.security_analyzer import AdvancedASTSecurityAnalyzer


@dataclass(frozen=True)
class SkillUploadCommand:
    zip_bytes: bytes


@dataclass(frozen=True)
class SkillUploadResult:
    status: str
    message: str
    skill_id: str
    pipeline_audit_logs: list[str]


@dataclass(frozen=True)
class ParsedSkillPackage:
    name: str
    description: str
    body: str
    frontmatter: dict[str, Any]
    bundled_files: dict[str, dict[str, Any]]
    version: str


class SkillValidationError(Exception):
    def __init__(
        self,
        *,
        status: str,
        message: str,
        logs: list[str],
        skill_id: str | None = None,
        http_status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.logs = logs
        self.skill_id = skill_id
        self.http_status_code = http_status_code


class SkillValidationService:
    MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
    MAX_FILE_COUNT = 200
    MAX_FILE_BYTES = 5 * 1024 * 1024
    MAX_TOTAL_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
    MAX_COMPRESSION_RATIO = 100

    _NAME_PATTERN = re.compile(r"^(?!-)(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*$")
    _FRONTMATTER_PATTERN = re.compile(
        r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(?P<body>.*)\Z",
        re.DOTALL,
    )
    _ALLOWED_FRONTMATTER_FIELDS = {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }

    def __init__(self, *, skill_repository=SkillRepository) -> None:
        self.skill_repository = skill_repository

    def parse_package(self, zip_bytes: bytes) -> ParsedSkillPackage:
        if len(zip_bytes) > self.MAX_ARCHIVE_BYTES:
            self._raise_validation(
                "Gói skill vượt quá kích thước upload cho phép.",
                f"Archive exceeds {self.MAX_ARCHIVE_BYTES} bytes.",
            )

        try:
            archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise SkillValidationError(
                status="REJECTED",
                message="File upload không phải định dạng zip hợp lệ.",
                logs=["File is not a valid zip file."],
            ) from exc

        with archive:
            files = self._validate_archive_entries(archive)
            skill_entries = [path for path in files if path.name == "SKILL.md"]
            if len(skill_entries) != 1:
                self._raise_validation(
                    "Gói skill phải chứa đúng một file có tên chính xác SKILL.md.",
                    f"Expected exactly one SKILL.md, found {len(skill_entries)}.",
                )

            skill_path = skill_entries[0]
            skill_root = skill_path.parent
            relative_files: dict[str, zipfile.ZipInfo] = {}
            for path, info in files.items():
                try:
                    relative_path = path.relative_to(skill_root)
                except ValueError:
                    self._raise_validation(
                        "Gói zip chứa file nằm ngoài thư mục skill.",
                        f"File outside skill root: {path}.",
                    )
                relative_files[relative_path.as_posix()] = info

            try:
                skill_md = archive.read(files[skill_path]).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SkillValidationError(
                    status="REJECTED",
                    message="SKILL.md phải sử dụng mã hóa UTF-8.",
                    logs=["SKILL.md is not valid UTF-8."],
                ) from exc

            frontmatter, body = self._parse_frontmatter(skill_md)
            name, description = self._validate_frontmatter(frontmatter, skill_root)
            bundled_files = self._read_bundled_files(
                archive,
                relative_files,
                skill_md_path="SKILL.md",
            )

        metadata = frontmatter.get("metadata") or {}
        return ParsedSkillPackage(
            name=name,
            description=description,
            body=body,
            frontmatter=frontmatter,
            bundled_files=bundled_files,
            version=metadata.get("version", "1.0.0"),
        )

    async def upload_skill(
        self,
        *,
        db: Session,
        llm_gateway,
        command: SkillUploadCommand,
    ) -> SkillUploadResult:
        logs = ["[VONG 1] Zip package parsing and validation started."]
        package = self.parse_package(command.zip_bytes)
        logs.append("[VONG 1] Passed Agent Skills package validation.")

        if self.skill_repository.get_skill_by_name(db, package.name):
            raise SkillValidationError(
                status="CONFLICT",
                message=(
                    f"Skill '{package.name}' đã tồn tại. "
                    "Bản đang hoạt động không bị thay thế bởi upload mới."
                ),
                logs=[f"Duplicate skill name: {package.name}."],
                http_status_code=409,
            )

        logs.append("[VONG 1] Static AST security scan and secret scan started.")
        is_malicious = self._scan_package(package, logs)
        if is_malicious:
            logs.append("[KET LUAN] Rejected by static security scan.")
            skill = self._persist_package_or_conflict(db, package)
            self.skill_repository.update_sandbox_result(
                db,
                skill_id=skill.id,
                status=SkillStatus.REJECTED.value,
                review_log="\n".join(logs),
                is_malicious=True,
            )
            raise SkillValidationError(
                status="REJECTED",
                message="Skill violates static security policy.",
                logs=logs,
                skill_id=str(skill.id),
            )

        logs.append("[VONG 1] Passed static security check.")
        logs.append("[VONG 2] Telecom taxonomy check started.")
        taxonomy_score = TelecomDomainValidator.calculate_taxonomy_score(
            package.name,
            package.description,
            package.body,
        )
        logs.append(f"[VONG 2] Taxonomy score: {taxonomy_score:.2f}.")

        logs.append("[VONG 3] LLM domain judge started.")
        try:
            llm_judge = await TelecomDomainValidator.invoke_llm_domain_judge(
                llm_gateway,
                package.name,
                package.description,
                package.body,
            )
        except Exception as exc:
            llm_judge = None
            logs.append(f"[VONG 3] LLM judge unavailable: {exc}.")
        else:
            logs.append(
                f"[VONG 3] LLM domain score: {llm_judge.domain_score:.2f}. "
                f"Reason: {llm_judge.reason}"
            )
            if llm_judge.suspicious_points and llm_judge.suspicious_points != "None":
                logs.append(f"[VONG 3] Suspicious points: {llm_judge.suspicious_points}")

        llm_score = llm_judge.domain_score if llm_judge is not None else 0.0
        if taxonomy_score < 0.25 and llm_score < 0.5:
            logs.append("[KET LUAN] Rejected because skill is outside telecom operations domain.")
            skill = self._persist_package_or_conflict(db, package)
            self.skill_repository.update_sandbox_result(
                db,
                skill_id=skill.id,
                status=SkillStatus.REJECTED.value,
                review_log="\n".join(logs),
                is_malicious=False,
            )
            raise SkillValidationError(
                status="REJECTED",
                message="Skill is outside the supported telecom operations domain.",
                logs=logs,
                skill_id=str(skill.id),
            )

        logs.append("[VONG 4] Pending human review.")
        skill = self._persist_package_or_conflict(db, package)
        self.skill_repository.update_sandbox_result(
            db,
            skill_id=skill.id,
            status=SkillStatus.TESTING.value,
            review_log="\n".join(logs),
            is_malicious=False,
        )
        return SkillUploadResult(
            status="PENDING_REVIEW",
            message="Skill passed automated validation and is waiting for human approval.",
            skill_id=str(skill.id),
            pipeline_audit_logs=logs,
        )

    def _validate_archive_entries(
        self, archive: zipfile.ZipFile
    ) -> dict[PurePosixPath, zipfile.ZipInfo]:
        files: dict[PurePosixPath, zipfile.ZipInfo] = {}
        total_size = 0
        for info in archive.infolist():
            if info.is_dir() or info.filename.startswith("__MACOSX/"):
                continue
            if len(files) >= self.MAX_FILE_COUNT:
                self._raise_validation(
                    "Gói skill chứa quá nhiều file.",
                    f"Archive exceeds {self.MAX_FILE_COUNT} files.",
                )
            if "\\" in info.filename:
                self._raise_validation(
                    "Đường dẫn trong gói skill không hợp lệ.",
                    f"Backslash is not allowed in archive path: {info.filename}.",
                )
            path = PurePosixPath(info.filename)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                self._raise_validation(
                    "Đường dẫn trong gói skill không hợp lệ.",
                    f"Unsafe archive path: {info.filename}.",
                )
            if path in files:
                self._raise_validation(
                    "Gói skill chứa đường dẫn file trùng lặp.",
                    f"Duplicate archive path: {info.filename}.",
                )
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                self._raise_validation(
                    "Gói skill không được chứa symbolic link.",
                    f"Symlink is not allowed: {info.filename}.",
                )
            if info.flag_bits & 0x1:
                self._raise_validation(
                    "Gói skill không được chứa file mã hóa.",
                    f"Encrypted archive entry: {info.filename}.",
                )
            if info.file_size > self.MAX_FILE_BYTES:
                self._raise_validation(
                    "Một file trong gói skill vượt quá kích thước cho phép.",
                    f"File exceeds {self.MAX_FILE_BYTES} bytes: {info.filename}.",
                )
            total_size += info.file_size
            if total_size > self.MAX_TOTAL_UNCOMPRESSED_BYTES:
                self._raise_validation(
                    "Tổng dữ liệu giải nén của skill vượt quá giới hạn.",
                    f"Archive exceeds {self.MAX_TOTAL_UNCOMPRESSED_BYTES} uncompressed bytes.",
                )
            if info.file_size and (
                not info.compress_size
                or info.file_size / info.compress_size > self.MAX_COMPRESSION_RATIO
            ):
                self._raise_validation(
                    "Gói skill có tỷ lệ nén không an toàn.",
                    f"Suspicious compression ratio: {info.filename}.",
                )
            files[path] = info
        return files

    def _parse_frontmatter(self, skill_md: str) -> tuple[dict[str, Any], str]:
        match = self._FRONTMATTER_PATTERN.fullmatch(skill_md)
        if not match:
            self._raise_validation(
                "SKILL.md phải có YAML frontmatter nằm giữa hai dòng '---'.",
                "Missing or malformed YAML frontmatter delimiters.",
            )
        try:
            parsed = yaml.safe_load(match.group("yaml"))
        except yaml.YAMLError as exc:
            raise SkillValidationError(
                status="REJECTED",
                message="YAML frontmatter trong SKILL.md không hợp lệ.",
                logs=[f"Failed to parse YAML frontmatter: {exc}"],
            ) from exc
        if not isinstance(parsed, dict):
            self._raise_validation(
                "YAML frontmatter phải là một mapping.",
                "Frontmatter must parse to an object.",
            )
        body = match.group("body").strip()
        if not body:
            self._raise_validation(
                "SKILL.md phải có phần hướng dẫn Markdown sau frontmatter.",
                "SKILL.md body is empty.",
            )
        return parsed, body

    def _validate_frontmatter(
        self,
        frontmatter: dict[str, Any],
        skill_root: PurePosixPath,
    ) -> tuple[str, str]:
        if any(not isinstance(key, str) for key in frontmatter):
            self._raise_validation(
                "Mọi key trong YAML frontmatter phải là chuỗi.",
                "Frontmatter contains a non-string key.",
            )
        unknown_fields = set(frontmatter) - self._ALLOWED_FRONTMATTER_FIELDS
        if unknown_fields:
            self._raise_validation(
                "SKILL.md chứa field frontmatter không thuộc Agent Skills specification.",
                f"Unknown frontmatter fields: {sorted(unknown_fields)}.",
            )

        name = frontmatter.get("name")
        if not isinstance(name, str) or not self._NAME_PATTERN.fullmatch(name) or len(name) > 64:
            self._raise_validation(
                "Tên skill phải gồm chữ thường, số và dấu '-', tối đa 64 ký tự, không có '--'.",
                f"Invalid Agent Skills name: {name!r}.",
            )
        if skill_root != PurePosixPath(".") and skill_root.name != name:
            self._raise_validation(
                "Tên skill phải trùng với tên thư mục chứa SKILL.md.",
                f"Skill name '{name}' does not match parent directory '{skill_root.name}'.",
            )

        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > 1024:
            self._raise_validation(
                "Description phải là chuỗi không rỗng và tối đa 1024 ký tự.",
                "Invalid Agent Skills description.",
            )

        license_value = frontmatter.get("license")
        if license_value is not None and (
            not isinstance(license_value, str) or not license_value.strip()
        ):
            self._raise_validation("Field license không hợp lệ.", "license must be a string.")

        compatibility = frontmatter.get("compatibility")
        if compatibility is not None and (
            not isinstance(compatibility, str)
            or not compatibility.strip()
            or len(compatibility) > 500
        ):
            self._raise_validation(
                "Compatibility phải là chuỗi từ 1 đến 500 ký tự.",
                "Invalid compatibility field.",
            )

        metadata = frontmatter.get("metadata")
        if metadata is not None and (
            not isinstance(metadata, dict)
            or any(not isinstance(key, str) for key in metadata)
            or any(not isinstance(value, str) for value in metadata.values())
        ):
            self._raise_validation(
                "Metadata phải là mapping từ chuỗi sang chuỗi.",
                "metadata must be a string-to-string mapping.",
            )

        allowed_tools = frontmatter.get("allowed-tools")
        if allowed_tools is not None and not isinstance(allowed_tools, str):
            self._raise_validation(
                "allowed-tools phải là một chuỗi.",
                "allowed-tools must be a string.",
            )
        return name, description.strip()

    def _read_bundled_files(
        self,
        archive: zipfile.ZipFile,
        relative_files: dict[str, zipfile.ZipInfo],
        *,
        skill_md_path: str,
    ) -> dict[str, dict[str, Any]]:
        bundled_files: dict[str, dict[str, Any]] = {}
        for path, info in relative_files.items():
            if path == skill_md_path:
                continue
            content = archive.read(info)
            media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                encoding = "base64"
                stored_content = base64.b64encode(content).decode("ascii")
            else:
                encoding = "utf-8"
                stored_content = text_content
            bundled_files[path] = {
                "encoding": encoding,
                "content": stored_content,
                "media_type": media_type,
                "size": len(content),
            }
        return bundled_files

    def _scan_package(self, package: ParsedSkillPackage, logs: list[str]) -> bool:
        is_malicious = False
        text_sources = {"SKILL.md": package.body}
        for path, record in package.bundled_files.items():
            if record["encoding"] == "utf-8":
                text_sources[path] = record["content"]
            if path.endswith(".py") and record["encoding"] == "utf-8":
                ast_clean, ast_errors = AdvancedASTSecurityAnalyzer.analyze_source_code(
                    record["content"]
                )
                if not ast_clean:
                    is_malicious = True
                    logs.extend([f"[{path} AST ERROR]: {error}" for error in ast_errors])

        for path, content in text_sources.items():
            for secret_type, pattern in AgentSafetyGuard.PII_AND_SECRET_PATTERNS.items():
                if pattern.search(content):
                    is_malicious = True
                    logs.append(
                        f"[{path} SECURITY ERROR]: Phát hiện chuỗi nhạy cảm/secret ({secret_type})."
                    )
        return is_malicious

    def _persist_package(self, db: Session, package: ParsedSkillPackage):
        return self.skill_repository.create_uploaded_skill(
            db=db,
            name=package.name,
            description=package.description,
            skill_md=package.body,
            frontmatter=package.frontmatter,
            bundled_files=package.bundled_files,
            version=package.version,
        )

    def _persist_package_or_conflict(self, db: Session, package: ParsedSkillPackage):
        try:
            return self._persist_package(db, package)
        except IntegrityError as exc:
            db.rollback()
            raise SkillValidationError(
                status="CONFLICT",
                message=(
                    f"Skill '{package.name}' đã tồn tại. "
                    "Bản đang hoạt động không bị thay thế bởi upload mới."
                ),
                logs=[f"Duplicate skill name: {package.name}."],
                http_status_code=409,
            ) from exc

    @staticmethod
    def _raise_validation(message: str, log: str) -> None:
        raise SkillValidationError(status="REJECTED", message=message, logs=[log])
