import unittest
from local_recognition.visual_labels import GROUP_THRESHOLDS, LABEL_GROUPS


class VisualLabelTests(unittest.TestCase):
    def test_groups_have_unique_stable_keys_and_chinese_labels(self):
        self.assertGreaterEqual(len(LABEL_GROUPS), 3)
        for definitions in LABEL_GROUPS.values():
            keys = [item[0] for item in definitions]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertTrue(all(item[1] and item[2] for item in definitions))

    def test_cta_and_logo_candidates_exist(self):
        ui_keys = {item[0] for item in LABEL_GROUPS["ui_state"]}
        self.assertIn("store_cta", ui_keys)
        self.assertIn("logo_title", ui_keys)

    def test_unreliable_groups_cannot_pass_threshold(self):
        self.assertIsNone(GROUP_THRESHOLDS["ui_state"])
        self.assertIsNone(GROUP_THRESHOLDS["effect_intensity"])
        self.assertIsNotNone(GROUP_THRESHOLDS["content_mode"])
        self.assertIsNotNone(GROUP_THRESHOLDS["subject_style"])


if __name__ == "__main__":
    unittest.main()
