import unittest

from local_recognition.schema import EvidenceValidationError, validate_evidence


def base(asset_type="video"):
    image = asset_type == "image"
    return {
        "schema_version": "0.1", "asset_name": "sample.png" if image else "sample.mp4",
        "asset_type": asset_type, "source_path": "oss://bucket/sample",
        "media": {"duration_sec": None if image else 10.0, "width": 720, "height": 1280,
                  "fps": None if image else 30.0, "has_audio": False if image else True,
                  "language_candidates": [], "file_hash": "abc"},
        "segments": [], "global_evidence": {}, "quality": {}, "runtime": {},
    }


class EvidenceSchemaTests(unittest.TestCase):
    def test_valid_video(self):
        data = base("video")
        data["segments"] = [{"segment_id": "seg_001", "start_sec": 0.0, "end_sec": 2.5,
            "representative_frames": ["f1.jpg"], "scene_change_reason": "cut",
            "ocr": [{"text_original": "立即下载", "confidence": 0.9,
                     "bbox": [0.1, 0.1, 0.9, 0.2], "evidence_frame": "f1.jpg"}],
            "speech": [], "visual": {}, "audio": {"speech_present": False,
            "music_present": True, "sound_effects_present": False, "energy": 0.5}}]
        self.assertEqual(validate_evidence(data), [])

    def test_valid_image(self):
        data = base("image")
        data["segments"] = [{"segment_id": "region_001", "region": "left",
            "bbox": [0.0, 0.0, 0.5, 1.0], "analysis_module": "角色展示",
            "ocr": [], "visual": {}, "confidence": 0.8}]
        self.assertEqual(validate_evidence(data), [])

    def test_image_rejects_fake_timeline_and_audio(self):
        data = base("image")
        data["segments"] = [{"segment_id": "region_001", "region": "full",
            "bbox": [0, 0, 1, 1], "analysis_module": "整体", "start_sec": 0,
            "end_sec": 3, "speech": [{"text_original": "x"}],
            "audio": {"music_present": True}}]
        paths = {x.path for x in validate_evidence(data, raise_on_error=False)}
        self.assertIn("$.segments[0]", paths)
        self.assertIn("$.segments[0].speech", paths)
        self.assertIn("$.segments[0].audio", paths)

    def test_rejects_inference_fields(self):
        data = base("video")
        data["segments"] = [{"segment_id": "seg_001", "start_sec": 0, "end_sec": 1,
            "ocr": [], "speech": [], "audio": {}, "visual": {}, "可借鉴点": "增加按钮"}]
        with self.assertRaises(EvidenceValidationError):
            validate_evidence(data)

    def test_video_without_audio_rejects_speech(self):
        data = base("video"); data["media"]["has_audio"] = False
        data["segments"] = [{"segment_id": "seg_001", "start_sec": 0, "end_sec": 1,
            "ocr": [], "speech": [{"text_original": "hello", "confidence": 0.8,
            "start_sec": 0.0, "end_sec": 0.8}], "audio": {"speech_present": True}, "visual": {}}]
        with self.assertRaises(EvidenceValidationError):
            validate_evidence(data)


if __name__ == "__main__":
    unittest.main()
