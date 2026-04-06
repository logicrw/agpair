from __future__ import annotations
import pathlib
from agpair.executors.local_cli import LocalCLIExecutor
from agpair.models import ContinuationCapability

class GeminiExecutor(LocalCLIExecutor):
    def __init__(self, gemini_bin: str = "gemini") -> None:
        super().__init__(
            bin_path=gemini_bin,
            backend_id="gemini_cli",
            build_cmd=self._build_gemini_cmd,
        )

    def _build_gemini_cmd(self, body: str, repo_path: str, temp_dir: pathlib.Path) -> list[str]:
        return [
            self.bin_path,
            "-y",
            "--output-format",
            "json",
            "-p",
            body,
        ]

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.UNSUPPORTED
