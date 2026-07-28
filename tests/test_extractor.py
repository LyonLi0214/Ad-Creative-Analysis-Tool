import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from local_recognition.extractor import extract_image_skeleton, extract_video_skeleton


class ExtractorTests(unittest.TestCase):
    def test_image_has_regions_without_timeline(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.png"
            cv2.imencode(".png", np.zeros((100, 200, 3), dtype=np.uint8))[1].tofile(str(path))
            evidence = extract_image_skeleton(path)
            self.assertEqual(evidence["asset_type"], "image")
            self.assertEqual(len(evidence["segments"]), 4)
            self.assertNotIn("start_sec", evidence["segments"][0])

    def test_video_detects_hard_cut(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
            for _ in range(20): writer.write(np.zeros((90, 160, 3), dtype=np.uint8))
            for _ in range(20): writer.write(np.full((90, 160, 3), (0, 255, 0), dtype=np.uint8))
            writer.release()
            evidence = extract_video_skeleton(path, output_dir=Path(td) / "frames",
                                              sample_interval_sec=0.2, cut_threshold=0.3,
                                              min_segment_sec=1.0)
            self.assertEqual(evidence["asset_type"], "video")
            self.assertFalse(evidence["media"]["has_audio"])
            self.assertGreaterEqual(len(evidence["segments"]), 2)


if __name__ == "__main__":
    unittest.main()
