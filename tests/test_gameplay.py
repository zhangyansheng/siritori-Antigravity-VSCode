"""
tests/test_gameplay.py - エンドツーエンドのゲーム進行テスト
"""

import unittest
import os
import sys
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_DIR = os.path.join(PROJECT_ROOT, "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from orchestrator import Orchestrator

class TestGameplay(unittest.TestCase):
    def test_single_turn_flow(self):
        orch = Orchestrator()
        orch.reset_state()

        # 開始シーケンス実行
        ok = orch.start_sequence()
        self.assertTrue(ok)
        self.assertEqual(orch.state["status"], "starting")
        self.assertIsNotNone(orch.state["start_char"])
        self.assertEqual(len(orch.state["player_turn_order"]), 3)

        # 第1ターン実行
        should_continue = orch.execute_turn()
        self.assertTrue(should_continue)
        self.assertEqual(orch.state["total_turns"], 1)

        # ターン記録の確認
        turns = orch.state["turns"]
        self.assertEqual(len(turns), 1)
        t1 = turns[0]
        self.assertEqual(t1["verdict"], "valid")
        self.assertIsNotNone(t1["answer"]["full_name"])
        self.assertGreater(t1["score"]["total"], 0)

        # スコアボードの確認
        p = t1["player"]
        self.assertGreater(orch.state["scores"][p], 0)

        # 次の必要頭文字列が設定されていること
        self.assertIsNotNone(orch.state["required_prefix"])

        # 終了宣告による停止テスト
        orch.request_stop()
        self.assertTrue(orch.state["stop_requested"])
        
        orch.reset_state()

if __name__ == "__main__":
    unittest.main()
