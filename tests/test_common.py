import io
import unittest
from contextlib import redirect_stderr

from gitstat.common import log, set_quiet


class LogTest(unittest.TestCase):
    def tearDown(self):
        set_quiet(False)

    def test_log_writes_to_stderr_by_default(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            log("hello")
        self.assertEqual(buf.getvalue(), "hello\n")

    def test_log_is_suppressed_in_quiet_mode(self):
        set_quiet(True)
        buf = io.StringIO()
        with redirect_stderr(buf):
            log("hello")
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
