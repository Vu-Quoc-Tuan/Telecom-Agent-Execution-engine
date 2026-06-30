# backend/app/sandbox/security_analyzer.py
from __future__ import annotations

import ast


class AdvancedASTSecurityAnalyzer:
    # ❌ DANH SÁCH ĐEN THƯ VIỆN CẤM IMPORT TRỰC TIẾP HOẶC GIÁN TIẾP
    # Bổ sung importlib/ctypes và các module nạp động / can thiệp bộ nhớ - tiến trình.
    DANGEROUS_IMPORTS = {
        "os",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "paramiko",
        # Mạng/giao thức: http.client, ftplib... vẫn mở kết nối ra ngoài dù không
        # đi qua socket trực tiếp -> chặn để script không exfiltrate dữ liệu.
        "http",
        "urllib3",
        "aiohttp",
        "websocket",
        "websockets",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "nntplib",
        "xmlrpc",
        "asyncore",
        "asynchat",
        "ssl",
        "webbrowser",
        # Đọc/ghi file qua API ngoài open(): pathlib.Path.read_text(), io.open(),
        # tempfile, glob... đều bypass được rào open() literal-only -> chặn cả module.
        "pathlib",
        "io",
        "tempfile",
        "fileinput",
        "glob",
        "fnmatch",
        "linecache",
        "sqlite3",
        "dbm",
        "shelve",
        "pickle",
        "marshal",
        "dill",
        "shutil",
        "sys",
        "builtins",
        "urllib",
        "importlib",
        "ctypes",
        "_ctypes",
        "gc",
        "code",
        "codeop",
        "pty",
        "platform",
        "asyncio",
        "concurrent",
        "multiprocessing",
        "threading",
        "_thread",
        "sched",
        "signal",
        "atexit",
        "resource",
        "mmap",
        "fcntl",
        "posix",
        "nt",
        "cffi",
        "inspect",
    }

    # ❌ DANH SÁCH ĐEN CÁC HÀM NGUY HIỂM CẤM TỰ KÍCH HOẠT VÌ CÓ NGUY CƠ CHẠY LẬU CODE ẨN
    DANGEROUS_CALLS = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "vars",
        "globals",
        "locals",
        "memoryview",
        "breakpoint",
        "input",
    }

    # ✅ open() được phép có điều kiện: skill script chạy trong sandbox cần đọc tham số
    # từ workspace (ví dụ args.json mà runner ghi vào /home/user/workspace/args.json).
    # Chỉ cho phép khi: file là chuỗi literal, đường dẫn tương đối an toàn trong workspace,
    # và mode là chỉ-đọc. Mọi dạng open() khác (biến, ghi/append, đường dẫn tuyệt đối,
    # vượt thư mục) đều bị chặn — xem _validate_open_call.

    # ✅ Một số dunder vô hại khi đứng ở dạng tên module (vd: `if __name__ == "__main__":`).
    # Khi đứng ở dạng thuộc tính (obj.__name__) thì vẫn chặn vì là mắt xích điều hướng class.
    BENIGN_NAME_DUNDERS = {"__name__", "__doc__", "__file__"}

    @classmethod
    def analyze_source_code(cls, code_text: str) -> tuple[bool, list[str]]:
        """Phân tích tĩnh toàn diện cấu trúc mã nguồn thô"""
        errors: list[str] = []
        try:
            tree = ast.parse(code_text)
        except SyntaxError as se:
            return False, [f"Lỗi cú pháp Python nghiêm trọng không thể parse: {str(se)}"]

        for node in ast.walk(tree):
            # 1. Bẫy các kiểu Import (import os hoặc from subprocess import Popen)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_module = alias.name.split(".")[0]
                    if root_module in cls.DANGEROUS_IMPORTS:
                        errors.append(
                            f"Dòng {node.lineno}: Vi phạm an ninh! Cấm import thư viện hệ thống '{alias.name}'."
                        )

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_module = node.module.split(".")[0]
                    if root_module in cls.DANGEROUS_IMPORTS:
                        errors.append(
                            f"Dòng {node.lineno}: Vi phạm an ninh! Cấm import từ phân vùng '{node.module}'."
                        )

            # 2. Bẫy các cuộc gọi hàm (Call Nodes)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    open_error = cls._validate_open_call(node)
                    if open_error is not None:
                        errors.append(open_error)
                elif isinstance(node.func, ast.Name) and node.func.id in cls.DANGEROUS_CALLS:
                    errors.append(
                        f"Dòng {node.lineno}: Tác vụ bị chặn! Không được phép tự gọi hàm nguyên bản '{node.func.id}'."
                    )

            # 3. Chặn "Python Jailbreak" qua thuộc tính dunder.
            # Bịt các vector: ().__class__.__bases__[0].__subclasses__(),
            # f.__init__.__globals__['os'], obj.__getattribute__(...), ...
            elif isinstance(node, ast.Attribute):
                if cls._is_dunder(node.attr):
                    errors.append(
                        f"Dòng {node.lineno}: Vi phạm an ninh! Cấm truy cập thuộc tính nội bộ "
                        f"nhạy cảm '{node.attr}' (kỹ thuật vượt rào sandbox)."
                    )

            # 4. Chặn tham chiếu tên dunder (vd: __builtins__['eval'], __import__, __loader__).
            #    Bao trùm cả dạng subscript __builtins__["eval"](...) vì value là ast.Name.
            elif isinstance(node, ast.Name):
                if cls._is_dunder(node.id) and node.id not in cls.BENIGN_NAME_DUNDERS:
                    errors.append(
                        f"Dòng {node.lineno}: Vi phạm an ninh! Cấm tham chiếu tên '{node.id}'."
                    )

            # 5. Bẫy hành vi cố tình đọc trộm file nhạy cảm thông qua hằng số chữ (String Literals)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                sensitive_paths = ["/etc/passwd", ".env", "id_rsa", "private_key", "/etc/shadow"]
                for path in sensitive_paths:
                    if path in node.value:
                        errors.append(
                            f"Dòng {node.lineno}: Cảnh báo nguy hiểm! Phát hiện chuỗi văn bản trỏ tới tệp tin tối mật của hệ điều hành: '{path}'."
                        )

        if errors:
            return False, errors
        return True, ["Mã nguồn vượt qua bài trắc nghiệm quét tĩnh AST an toàn."]

    @staticmethod
    def _is_dunder(identifier: str) -> bool:
        """True nếu định danh có dạng __xxx__ (thuộc tính/biến nội bộ của Python)."""
        return len(identifier) > 4 and identifier.startswith("__") and identifier.endswith("__")

    @classmethod
    def _validate_open_call(cls, node: ast.Call) -> str | None:
        """Kiểm tra một lời gọi open(). Trả về thông báo lỗi nếu không an toàn, None nếu hợp lệ.

        Chỉ cho phép đọc file tham số trong workspace sandbox, ví dụ
        ``open("args.json")`` hoặc ``open("data/lookup.csv")``. Yêu cầu:
        - tham số path là chuỗi literal (không phải biến/biểu thức),
        - path tương đối, không tuyệt đối, không chứa '..' hoặc backslash,
        - mode (nếu có) phải là literal chỉ-đọc, không 'w'/'a'/'x'/'+'.
        """
        line = node.lineno
        deny = (
            f"Dòng {line}: Tác vụ bị chặn! open() chỉ được phép đọc file tham số "
            f"tương đối trong workspace (vd args.json) ở chế độ chỉ-đọc."
        )

        if not node.args:
            return deny
        path_node = node.args[0]
        if not (isinstance(path_node, ast.Constant) and isinstance(path_node.value, str)):
            return deny
        path_value = path_node.value
        if (
            not path_value
            or path_value.startswith("/")
            or "\\" in path_value
            or ".." in path_value.split("/")
            or path_value.startswith("~")
        ):
            return deny

        mode_value: str | None = None
        if len(node.args) >= 2:
            mode_node = node.args[1]
            if not (isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str)):
                return deny
            mode_value = mode_node.value
        for keyword in node.keywords:
            if keyword.arg == "mode":
                if not (
                    isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)
                ):
                    return deny
                mode_value = keyword.value.value

        if mode_value is not None:
            normalized = mode_value.replace("t", "").replace("b", "")
            if normalized not in {"r"}:
                return deny
        return None
