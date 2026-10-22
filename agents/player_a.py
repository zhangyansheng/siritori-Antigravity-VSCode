"""
agents/player_a.py - PlayerA（戦略家）
勝利最優先: 次の手番が答えにくい語尾（る、ぬ、ん、拗音など）や長い名称を狙う。
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

# 返しにくい語尾
DIFFICULT_ENDINGS = {'る', 'ぬ', 'ん', 'へ', 'ほ', 'ら', 'り', 'れ', 'ろ'}

class PlayerA(BasePlayer):
    def __init__(self):
        super().__init__("PlayerA", "戦略家")

    def score_candidate(self, record: SourceRecord, identifier: str, suffix: str, reading: str, next_prefixes: List[str]) -> float:
        score = 0.0
        # 1. 識別子の長さ（基本点＋戦略的価値）
        score += len(identifier) * 1.5

        # 2. 次の手番の返しにくさ
        if next_prefixes:
            target_prefix = next_prefixes[0]
            # 語尾の文字
            last_char = reading[-1] if reading else ""
            if last_char in DIFFICULT_ENDINGS or target_prefix in DIFFICULT_ENDINGS:
                score += 5.0
            if "ん" in target_prefix:
                score += 4.0
            if len(target_prefix) > 1: # 拗音など
                score += 3.0

        return score

    def generate_comment(self, chosen: SourceRecord, identifier: str, suffix: str) -> str:
        return f"相手の手詰まりを狙い、戦略的語尾を持つ『{chosen.title}』を選択しました。"

if __name__ == "__main__":
    player = PlayerA()
    player.run()
