"""启动器测试:通过软链接调用时仍能定位 panel_cli 包(README 推荐的 PATH 安装方式)。"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "bin" / "1panel-cli"


class TestLauncherSymlink(unittest.TestCase):
    def test_runs_through_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "1panel-cli"
            os.symlink(LAUNCHER, link)
            result = subprocess.run(
                [sys.executable, str(link), "--version"],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1panel-cli", result.stdout)


if __name__ == "__main__":
    unittest.main()
