import tempfile
import unittest
from pathlib import Path

from gitstat.gitdata import load_config


class LoadConfigTest(unittest.TestCase):
    def test_empty_file_is_treated_as_no_config(self):
        # `gitstat --print-names > ~/.gitstat.json` truncates the file before gitstat reads it.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".gitstat.json"
            p.write_text("\n")
            self.assertEqual(load_config(str(p)), {})

    def test_reads_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".gitstat.json"
            p.write_text('{"names": {"a": "A"}}')
            self.assertEqual(load_config(str(p)), {"names": {"a": "A"}})


if __name__ == "__main__":
    unittest.main()
