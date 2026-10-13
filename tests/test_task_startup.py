"""端口拒绝的清楚诊断；实际系统分配端口见进程测试。"""
import contextlib
import io
import tempfile
import unittest
from unittest import mock

from learnradar.task_cli import main


class StartupTests(unittest.TestCase):
    def test_binding_failure_explains_error_and_preserves_data_directory(self):
        for code in (10013,10048):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                error=OSError(code,"fixture bind denied")
                error.winerror=code
                output=io.StringIO()
                with mock.patch("learnradar.web.serve",side_effect=error),contextlib.redirect_stdout(output):
                    result=main(["web","--demo","--data-dir",directory,"--port","8765"])
                self.assertEqual(result,1)
                self.assertIn(str(code),output.getvalue())
                self.assertIn("--port 0",output.getvalue())
                self.assertIn(directory,output.getvalue())
                self.assertNotIn("Traceback",output.getvalue())
