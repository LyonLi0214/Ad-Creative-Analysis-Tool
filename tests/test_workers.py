import unittest
from unittest.mock import patch
from local_recognition.recognize_asset import _run_module

class WorkerOrchestrationTests(unittest.TestCase):
    @patch("local_recognition.recognize_asset.subprocess.run")
    def test_worker_uses_current_python_and_module(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        _run_module("local_recognition.ocr_worker", ["--tasks", "in.json"])
        self.assertEqual(run.call_args.args[0][1:3], ["-m", "local_recognition.ocr_worker"])

    @patch("local_recognition.recognize_asset.subprocess.run")
    def test_worker_failure_is_not_silent(self, run):
        run.return_value.returncode = 7
        run.return_value.stdout = "out"
        run.return_value.stderr = "bad"
        with self.assertRaisesRegex(RuntimeError, "bad"):
            _run_module("local_recognition.asr_worker", ["asset.mp4"])

if __name__ == "__main__":
    unittest.main()
