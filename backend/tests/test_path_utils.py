import unittest

from app.common.exceptions import SkillRuntimeError
from app.common.utils import normalize_safe_relative_posix_path


class NormalizeSafeRelativePosixPathTests(unittest.TestCase):
    def test_normalizes_a_trimmed_nested_posix_path(self) -> None:
        self.assertEqual(
            "references/checklist.txt",
            normalize_safe_relative_posix_path("  references/checklist.txt  "),
        )

    def test_rejects_unsafe_paths(self) -> None:
        for raw in ("", "/etc/passwd", "../secret.txt", "scripts/./run.py", r"scripts\run.py"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_safe_relative_posix_path(raw)

    def test_read_skill_file_keeps_its_resource_error_context(self) -> None:
        from app.agent.builtin_runners import _run_read_skill_file

        with self.assertRaisesRegex(SkillRuntimeError, "Đường dẫn resource không hợp lệ"):
            _run_read_skill_file(object(), {"file_path": "../secret.txt"})

    def test_docker_executor_keeps_its_script_error_context(self) -> None:
        from app.sandbox.docker_executor import DockerSandboxExecutor

        with self.assertRaisesRegex(SkillRuntimeError, "Đường dẫn script không hợp lệ"):
            DockerSandboxExecutor._safe_relative_path("../secret.txt")


if __name__ == "__main__":
    unittest.main()
