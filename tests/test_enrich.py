import unittest

from local_recognition.enrich import (attach_image_ocr, attach_speech, attach_video_ocr,
    attach_visual_candidates, derive_cta_from_ocr)
from test_evidence_schema import base


class EnrichTests(unittest.TestCase):

    def test_visual_candidates_are_not_promoted_to_confirmed_facts(self):
        evidence = base("image")
        evidence["segments"] = [{"segment_id": "region_001", "region": "full",
            "bbox": [0, 0, 1, 1], "analysis_module": "整体构图", "ocr": [],
            "visual": {}, "confidence": 1.0}]
        result = {"region_001": {"model": "clip-test", "evidence_path": "asset.png",
            "evidence_bbox": [0, 0, 1, 1], "groups": {
            "content_mode": {"top_label": "gameplay", "top_label_zh": "实机玩法",
                "relative_score": 0.8, "margin": 0.4, "accepted": True,
                "candidates": [{"label": "gameplay", "label_zh": "实机玩法", "relative_score": 0.8}]}
        }}}
        attach_visual_candidates(evidence, result)
        self.assertEqual(evidence["quality"]["visual_coverage"], 1.0)
        self.assertEqual(evidence["quality"]["visual_confirmed_fact_coverage"], 0.0)
        item = evidence["segments"][0]["visual"]["semantic_candidates"][0]
        self.assertEqual(item["calibration_status"], "calibrating")

    def test_cta_requires_strong_ocr_evidence(self):
        evidence = base("image")
        evidence["segments"] = [{"segment_id": "region_001", "region": "full",
            "bbox": [0, 0, 1, 1], "analysis_module": "整体构图", "visual": {},
            "confidence": 1.0, "ocr": [
                {"text_original": "今すぐ", "bbox": [0.1, 0.8, 0.3, 0.9], "confidence": 0.95}
            ]}]
        derive_cta_from_ocr(evidence)
        self.assertTrue(evidence["global_evidence"]["cta_signal_present"])
        self.assertFalse(evidence["global_evidence"]["cta_present"])

    def test_store_badge_ocr_confirms_cta(self):
        evidence = base("image")
        evidence["segments"] = [{"segment_id": "region_001", "region": "full",
            "bbox": [0, 0, 1, 1], "analysis_module": "整体构图", "visual": {},
            "confidence": 1.0, "ocr": [
                {"text_original": "Google Play", "bbox": [0.1, 0.8, 0.4, 0.9], "confidence": 0.91}
            ]}]
        derive_cta_from_ocr(evidence)
        self.assertTrue(evidence["global_evidence"]["cta_present"])

    def test_image_ocr_is_assigned_by_region(self):
        data = base("image")
        data["segments"] = [
            {"segment_id": "region_001", "region": "top", "bbox": [0,0,1,.5],
             "analysis_module": "上部", "ocr": [], "visual": {}, "confidence": 1},
            {"segment_id": "region_002", "region": "bottom", "bbox": [0,.5,1,1],
             "analysis_module": "底部", "ocr": [], "visual": {}, "confidence": 1},
        ]
        attach_image_ocr(data, [{"text_original":"下载", "language":"zh",
            "text_normalized":"下载", "bbox":[.1,.7,.4,.8], "confidence":.9}])
        self.assertEqual(data["segments"][0]["ocr"], [])
        self.assertEqual(data["segments"][1]["ocr"][0]["text_original"], "下载")

    def test_video_ocr_adds_evidence_frame(self):
        data = base("video")
        data["segments"] = [{"segment_id":"seg_001", "start_sec":0, "end_sec":2,
            "representative_frames":["f.jpg"], "ocr":[], "speech":[], "visual":{}, "audio":{}}]
        attach_video_ocr(data, {"f.jpg":[{"text_original":"勝利", "language":"ja",
            "text_normalized":"勝利", "bbox":[0,0,1,1], "confidence":.95}]})
        self.assertEqual(data["segments"][0]["ocr"][0]["evidence_frame"], "f.jpg")

    def test_speech_overlaps_segments(self):
        data = base("video")
        data["segments"] = [
            {"segment_id":"seg_001", "start_sec":0, "end_sec":5,
             "ocr":[], "speech":[], "visual":{}, "audio":{}},
            {"segment_id":"seg_002", "start_sec":5, "end_sec":10,
             "ocr":[], "speech":[], "visual":{}, "audio":{}},
        ]
        item={"text_original":"テスト", "language":"ja", "start_sec":4,
              "end_sec":6, "confidence":.8}
        attach_speech(data, [item], "ja")
        self.assertEqual(len(data["segments"][0]["speech"]), 1)
        self.assertEqual(len(data["segments"][1]["speech"]), 1)


if __name__ == "__main__": unittest.main()
