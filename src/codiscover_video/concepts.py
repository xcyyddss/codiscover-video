"""Small, inspectable experience vocabulary shared by intent parsing and retrieval.

This is a phase-one lexical normalization table, not a learned user model. User
phrases and content matching differ: curiosity alone does not imply science.
"""

import re

PREFERENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "安静陪伴": ("安静", "陪伴", "放松", "舒缓", "治愈", "低刺激"),
    "慢节奏": ("慢节奏", "节奏慢", "慢一点"),
    "自然": ("自然", "雨声", "森林", "山林", "散步", "鸟鸣"),
    "轻度科普": ("科普", "知识"),
    "轻度好奇": ("好奇", "新鲜感", "观察"),
    "生活料理": ("做饭", "料理", "烹饪", "美食"),
    "手工": ("手工", "制作模型", "木工"),
    "游戏": ("游戏", "电竞"),
    "影视故事": ("电影", "影视", "故事"),
    "高刺激": ("刺激", "快节奏", "吵闹", "大喊", "爆炸"),
    "高信息密度": ("高信息密度", "学习压力", "信息太多", "学太多", "复杂推导"),
}

CONTENT_ALIASES = {
    **PREFERENCE_ALIASES,
    "安静陪伴": ("安静", "陪伴", "舒缓", "治愈", "低刺激"),
    "轻度科普": ("科普", "知识", "科学", "学习"),
    "高信息密度": ("高信息密度", "学习压力", "高认知负担", "复杂推导", "公式推导", "密集剧情", "课后练习"),
}


def preference_matches(tag: str, text: str) -> bool:
    """Match canonical concepts without treating '低刺激'/'没有旁白' as their opposite."""
    text = text.casefold()
    for alias in (tag, *CONTENT_ALIASES.get(tag, ())):
        alias = alias.casefold().strip()
        if not alias:
            continue
        for found in re.finditer(re.escape(alias), text):
            prefix = text[max(0, found.start() - 5) : found.start()]
            if re.search(r"(?:没有|并不|无需|避免|拒绝|不|无|低)(?:太|很|任何)?\s*$", prefix):
                continue
            return True
    return False
