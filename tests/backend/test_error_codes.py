import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))
import backend


class ErrorCodeTests(unittest.TestCase):
    def test_catalog_is_snake_case_and_documented(self):
        document = (Path(__file__).resolve().parents[2] / "docs/architecture/error-codes.md").read_text()
        for code in backend.STABLE_ERROR_CODES:
            self.assertRegex(code, r"^[a-z][a-z0-9_]*$")
            self.assertIn(f"| `{code}` |", document)

    def test_scoped_method_code_sets_are_stable(self):
        scoped = {
            "security": {"invalid", "failed", "timeout", "busy", "cli_missing", "daemon_down", "not_logged_in", "version_unsupported"},
            "exposure": {"unavailable", "timeout", "failed"},
            "weather": {"weather"},
            "calculator": {"invalid"},
            "units": {"invalid"},
            "files": {"invalid", "unavailable", "timeout", "failed"},
        }
        for codes in scoped.values():
            self.assertTrue(codes <= set(backend.STABLE_ERROR_CODES))
            self.assertTrue(all(re.fullmatch(r"[a-z][a-z0-9_]*", code) for code in codes))
