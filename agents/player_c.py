"""
agents/player_c.py - PlayerC（ユーモア・豆知識）
意外性・豆知識重視: 人物名・地名・組織名・一揆・変など、珍しい分類や興味深いトリビアを優先。
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

class PlayerC(BasePlayer):
    def __init__(self):
        super().__init__("PlayerC", "ユーモア")

    def score_candidate(self, record: SourceRecord, identifier: str, suffix: str, reading: str, next_prefixes: List[str]) -> float:
        score = 0.0
        # 1. 珍しい分類や人物・一揆などを好む
        if not suffix: # 人物、地名、組織名など
            score += 6.0
        elif suffix in ["一揆", "変"]:
            score += 5.0
        else:
            score += 2.0

        # 2. 豆知識やトリビア性（文字数の意外性やエピソード）
        if "一揆" in record.title or "変" in record.title:
            score += 3.0

        return score

    def generate_comment(self, chosen: SourceRecord, identifier: str, suffix: str) -> str:
        return f"ここは意外性を突いて観客を沸かせる『{chosen.title}』で勝負です！"

if __name__ == "__main__":
    player = PlayerC()
    player.run()
