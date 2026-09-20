"""
Generic Tool Router for AndroidAgentBridge.

Phase 1:
- list_files
- read_file
- write_file
- make_directory

No shell execution in this phase.
"""

from pathlib import Path


class ToolRouter:
    def __init__(self, workspace=None):
        self.workspace = Path(
            workspace or "/sdcard/AndroidAgentWorkspace"
        ).resolve()

        self.workspace.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _safe_path(self, path):
        """
        Resolve a path inside the configured workspace.
        Prevent path traversal outside the workspace.
        """
        candidate = (self.workspace / path).resolve()

        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            return None

        return candidate

    def list_files(self, path="."):
        directory = self._safe_path(path)

        if directory is None:
            return {
                "ok": False,
                "error": "path is outside the agent workspace",
            }

        if not directory.exists():
            return {
                "ok": False,
                "error": "directory_not_found",
            }

        if not directory.is_dir():
            return {
                "ok": False,
                "error": "not_a_directory",
            }

        items = []

        for item in sorted(
            directory.iterdir(),
            key=lambda p: p.name.lower(),
        ):
            items.append({
                "name": item.name,
                "type": "directory"
                if item.is_dir()
                else "file",
            })

        return {
            "ok": True,
            "path": str(directory),
            "items": items,
        }

    def read_file(self, path):
        file_path = self._safe_path(path)

        if file_path is None:
            return {
                "ok": False,
                "error": "path is outside the agent workspace",
            }

        if not file_path.exists():
            return {
                "ok": False,
                "error": "file_not_found",
            }

        if not file_path.is_file():
            return {
                "ok": False,
                "error": "not_a_file",
            }

        try:
            content = file_path.read_text(
                encoding="utf-8"
            )
        except UnicodeDecodeError:
            return {
                "ok": False,
                "error": "file_not_utf8",
            }

        return {
            "ok": True,
            "path": str(file_path),
            "content": content,
        }

    def make_directory(self, path):
        directory = self._safe_path(path)

        if directory is None:
            return {
                "ok": False,
                "error": "path is outside the agent workspace",
            }

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        return {
            "ok": True,
            "path": str(directory),
        }

    def write_file(self, path, content):
        file_path = self._safe_path(path)

        if file_path is None:
            return {
                "ok": False,
                "error": "path is outside the agent workspace",
            }

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        file_path.write_text(
            str(content),
            encoding="utf-8",
        )

        return {
            "ok": True,
            "path": str(file_path),
            "bytes": file_path.stat().st_size,
        }

    def build_project(self, project_path, timeout_seconds=300):
        project_dir = self._safe_path(project_path)

        if project_dir is None:
            return {
                "ok": False,
                "error": "path is outside the agent workspace",
            }

        if not project_dir.is_dir():
            return {
                "ok": False,
                "error": "project directory does not exist",
            }

        gradlew = project_dir / "gradlew"
        gradlew_bat = project_dir / "gradlew.bat"
        settings_gradle = project_dir / "settings.gradle"
        settings_kts = project_dir / "settings.gradle.kts"

        if not (gradlew.exists() or gradlew_bat.exists()):
            return {
                "ok": False,
                "error": "gradle wrapper not found",
            }

        if not (settings_gradle.exists() or settings_kts.exists()):
            return {
                "ok": False,
                "error": "gradle settings file not found",
            }

        try:
            timeout = max(30, min(int(timeout_seconds), 900))
        except (TypeError, ValueError):
            timeout = 300

        import subprocess

        if gradlew.exists():
            command = ["sh", "./gradlew", "assembleDebug"]
        else:
            command = ["cmd", "/c", "gradlew.bat", "assembleDebug"]

        try:
            completed = subprocess.run(
                command,
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            output = (
                completed.stdout[-12000:]
                + "\n"
                + completed.stderr[-12000:]
            )

            if completed.returncode != 0:
                return {
                    "ok": False,
                    "error": "build_failed",
                    "returncode": completed.returncode,
                    "output": output,
                }

            apks = [
                str(x.relative_to(project_dir))
                for x in project_dir.rglob("*.apk")
            ]

            return {
                "ok": True,
                "message": "build completed",
                "apks": apks,
                "output": output,
            }

        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "build_timeout",
                "timeout_seconds": timeout,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": "build_exception",
                "message": str(e),
            }

    def test_project(self, project_path, timeout_seconds=300):
        project_dir = self._safe_path(project_path)

        if project_dir is None:
            return {
                "ok": False,
                "error": "path is outside the agent workspace",
            }

        if not project_dir.is_dir():
            return {
                "ok": False,
                "error": "project directory does not exist",
            }

        gradlew = project_dir / "gradlew"
        gradlew_bat = project_dir / "gradlew.bat"

        if not (gradlew.exists() or gradlew_bat.exists()):
            return {
                "ok": False,
                "error": "gradle wrapper not found",
            }

        try:
            timeout = max(30, min(int(timeout_seconds), 900))
        except (TypeError, ValueError):
            timeout = 300

        import subprocess

        if gradlew.exists():
            command = ["sh", "./gradlew", "test"]
        else:
            command = ["cmd", "/c", "gradlew.bat", "test"]

        try:
            completed = subprocess.run(
                command,
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            output = (
                completed.stdout[-12000:]
                + "\n"
                + completed.stderr[-12000:]
            )

            return {
                "ok": completed.returncode == 0,
                "error": None if completed.returncode == 0 else "test_failed",
                "returncode": completed.returncode,
                "output": output,
            }

        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "test_timeout",
                "timeout_seconds": timeout,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": "test_exception",
                "message": str(e),
            }

    def execute(self, tool, arguments=None):
        arguments = arguments or {}

        if tool == "list_files":
            return self.list_files(
                arguments.get("path", ".")
            )

        if tool == "read_file":
            return self.read_file(
                arguments.get("path", "")
            )

        if tool == "make_directory":
            return self.make_directory(
                arguments.get("path", "")
            )

        if tool == "write_file":
            return self.write_file(
                arguments.get("path", ""),
                arguments.get("content", ""),
            )

        if tool == "build_project":
            return self.build_project(
                arguments.get("project_path", ""),
                arguments.get("timeout_seconds", 300),
            )

        if tool == "test_project":
            return self.test_project(
                arguments.get("project_path", ""),
                arguments.get("timeout_seconds", 300),
            )

        return {
            "ok": False,
            "error": "unknown_tool",
            "tool": tool,
        }


def create_tool_router(workspace=None):
    return ToolRouter(workspace)
