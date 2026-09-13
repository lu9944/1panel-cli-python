"""doctor 模块测试:显式 --env 路径识别。"""

import tempfile
import unittest
from pathlib import Path

from panel_cli import doctor


class TestFindEnvFile(unittest.TestCase):
    def test_explicit_path_is_used(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env") as handle:
            self.assertEqual(doctor.find_env_file(handle.name), Path(handle.name))

    def test_explicit_missing_path(self):
        self.assertIsNone(doctor.find_env_file("/no/such/panel-cli.env"))

    def test_search_without_explicit_path(self):
        found = doctor.find_env_file()
        self.assertTrue(found is None or found.exists())


if __name__ == "__main__":
    unittest.main()
