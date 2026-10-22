"""
agents/player_b.py - PlayerB（歴史学者）
教育的価値優先: 合戦名・戦い名・乱・戦争名・条約名など、歴史的意義の深い出来事を重視。
"""

import sys
import os
from typing import List

# パス解決
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agents.base_player import BasePlayer
from shared.history_sources import SourceRecord

PREF_CATEGORIES = {"合戦名", "戦い名", "乱", "戦争名", "条約名", "事件名"}

class PlayerB(BasePlayer):
    def __init__(self):
        super().__init__("PlayerB", "歴史学者")

    def score_candidate(self, record: SourceRecord, identifier: str, suffix: str, reading: str, next_prefixes: List[str]) -> float:
        score = 0.0
        # 1. カテゴリ適合度（合戦や戦争、条約を好む）
        if suffix in ["合戦", "戦い", "戦争", "条約", "乱"]:
            score += 6.0
        elif suffix in ["変", "事変", "事件"]:
            score += 4.0

        # 2. 概要の充実度（教育的価値）
        if record.extract:
            score += min(len(record.extract) / 50.0, 5.0)

        return score

    def generate_comment(self, chosen: SourceRecord, identifier: str, suffix: str) -> str:
        return f"日本史の転換点として極めて教育的意義の高い『{chosen.title}』を挙げます。"

if __name__ == "__main__":
    player = PlayerB()
    player.run()
