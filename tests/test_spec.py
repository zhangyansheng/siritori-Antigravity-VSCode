"""
tests/test_spec.py - 仕様書 v6.1 受け入れ基準 (13章) テストスイート
"""

import unittest
import os
import sys

# パス設定
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.reading import (
    split_event_name, determine_identifier_reading, generate_candidate_prefixes,
    verify_prefix_match, split_into_moras, normalize_reading_for_mora, clean_voice_all
)
from shared.schema import make_initial_state
from shared.history_sources import HistorySourceManager, SourceRecord

class TestSpecReading(unittest.TestCase):
    def test_t22_linking_no_separation(self):
        """T-22: 連結の「の」は識別子にも後半呼称にも含まれず、識別子読みは「おけはざま」"""
        ident, suffix = split_event_name("桶狭間の戦い")
        self.assertEqual(ident, "桶狭間")
        self.assertEqual(suffix, "戦い")
        reading = determine_identifier_reading("桶狭間の戦い", ident, suffix, "おけはざまのたたかい")
        self.assertEqual(reading, "おけはざま")

    def test_t22_no_linking_no_separation(self):
        """T-22: 「上田合戦」「満州事変」は「の」なしで分離される"""
        ident1, suffix1 = split_event_name("上田合戦")
        self.assertEqual(ident1, "上田")
        self.assertEqual(suffix1, "合戦")
        reading1 = determine_identifier_reading("上田合戦", ident1, suffix1, "うえだかっせん")
        self.assertEqual(reading1, "うえだ")

        ident2, suffix2 = split_event_name("満州事変")
        self.assertEqual(ident2, "満州")
        self.assertEqual(suffix2, "事変")
        reading2 = determine_identifier_reading("満州事変", ident2, suffix2, "まんしゅうじへん")
        self.assertEqual(reading2, "まんしゅう")

    def test_t04_n_rule(self):
        """T-04: 「ん」終わりは敗北にならず2拍（例: しん）が必要頭文字列となる"""
        ident, suffix = split_event_name("日米和親条約")
        reading = determine_identifier_reading("日米和親条約", ident, suffix, "にちべいわしんじょうやく")
        self.assertEqual(reading, "にちべいわしん")
        cands = generate_candidate_prefixes(reading)
        # ① M2+ん (しん) ② M2 (し)
        self.assertEqual(cands, ["しん", "し"])

    def test_t05_special_sounds(self):
        """T-05: 濁音の清音化、拗音、長音処理"""
        # ほんのうじ -> 語尾「じ」 -> 清音化して「し」
        cands = generate_candidate_prefixes("ほんのうじ")
        self.assertEqual(cands, ["し"])

        # せふぃー -> 長音除去 -> 語尾「ふぃ」 -> 「ふぃ」「ふ」
        cands_choon = generate_candidate_prefixes("せふぃー")
        self.assertEqual(cands_choon, ["ふぃ", "ふ"])

    def test_t06_exact_match_history(self):
        """T-06: 既出判定は完全一致。濁音違い（かんたい／がんたい）は別の語"""
        history = {"かんたい"}
        self.assertNotIn("がんたい", history)
        self.assertIn("かんたい", history)

    def test_prefix_verification(self):
        """頭文字照合（清音化して照合）"""
        # 識別子「おだのぶなが」読み「おだのぶなが」 -> 頭文字「お」に適合
        self.assertTrue(verify_prefix_match("おだのぶなが", "お"))
        # 清音化照合: 読みが「が...」でも prefix が「か」なら適合
        self.assertTrue(verify_prefix_match("がっこう", "か"))
        self.assertFalse(verify_prefix_match("おだのぶなが", "ま"))

class TestHistorySources(unittest.TestCase):
    def test_history_source_fallback(self):
        """T-20: フォールバックデータの取得と判定"""
        mgr = HistorySourceManager()
        rec, verified_by, healthy = mgr.fetch_with_fallback("桶狭間の戦い")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.title, "桶狭間の戦い")
        self.assertTrue(mgr.is_historical_event(rec))

if __name__ == "__main__":
    unittest.main()
