"""Controlled visual-semantic label groups for advertising creative evidence.

English prompts are used for CLIP matching; stable Chinese labels are stored in
evidence. Scores are relative similarities within one group, not probabilities
that a fact is true.
"""
from __future__ import annotations

LABEL_GROUPS = {
    "content_mode": [
        ("character_showcase", "角色展示", "an advertisement mainly showcasing an anime game character"),
        ("gameplay", "实机玩法", "actual mobile game gameplay footage"),
        ("combat", "战斗场景", "a mobile game combat or boss battle scene"),
        ("story_dialogue", "剧情对话", "a visual novel story or character dialogue scene"),
        ("menu_ui", "菜单界面", "a mobile game menu or selection interface"),
        ("reward_result", "奖励结算", "a game reward, victory, result, or item acquisition screen"),
        ("promotional_card", "宣传卡片", "a promotional end card for a mobile game advertisement"),
    ],
    "subject_style": [
        ("anime_character", "动漫角色", "a full-size anime game character"),
        ("chibi_character", "Q版角色", "a chibi or super-deformed game character"),
        ("real_person", "真人", "a real human person"),
        ("game_environment", "游戏场景", "a game environment without a prominent character"),
        ("items_rewards", "道具奖励", "game items, currency, gifts, or reward icons"),
        ("mixed_subjects", "混合主体", "multiple mixed game characters, interface, and environment"),
    ],
    "ui_state": [
        ("gameplay_hud", "战斗HUD", "an in-game combat HUD with health bars and skill buttons"),
        ("choice_buttons", "选择按钮", "a game interface with selectable choices or option buttons"),
        ("result_panel", "结算面板", "a victory, defeat, score, or result panel"),
        ("reward_panel", "福利面板", "a reward, gift, login bonus, or item claim panel"),
        ("store_cta", "商店下载CTA", "an app download call-to-action with App Store or Google Play badges"),
        ("logo_title", "Logo标题", "a game logo or title screen"),
        ("no_prominent_ui", "无突出UI", "an image without a prominent user interface"),
    ],
    "effect_intensity": [
        ("strong_effects", "强特效", "intense colorful combat effects, explosions, or magical particles"),
        ("moderate_effects", "中等特效", "some visible game effects or particles"),
        ("minimal_effects", "弱或无特效", "a calm image with minimal visual effects"),
    ],
}

DEFAULT_MIN_SCORE = 0.40
DEFAULT_MIN_MARGIN = 0.06

# Calibrated on the first manually reviewed 30-sample set. These are not
# validated production thresholds until they pass an independent holdout set.
# None means the group is experimental and cannot pass the evidence threshold.
GROUP_THRESHOLDS = {
    "content_mode": (0.80, 0.32),
    "subject_style": (0.45, 0.16),
    "ui_state": None,
    "effect_intensity": None,
}
