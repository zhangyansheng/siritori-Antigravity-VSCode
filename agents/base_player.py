"""
agents/base_player.py - Player エージェント共通ロジック
"""

import os
import sys
import json
import re
from typing import Dict, Any, List, Optional, Callable

# vendor ディレクトリのロード
VENDOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vendor"))
if os.path.exists(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from shared.schema import (
    TurnReply, AnswerData, EventInfo,
    SUFFIX_CATEGORIES, VALID_CATEGORIES, current_iso_time, generate_message_id
)
from shared.reading import (
    split_event_name, determine_identifier_reading, verify_prefix_match,
    generate_candidate_prefixes, split_into_moras
)
from shared.history_sources import HistorySourceManager, SourceRecord, FALLBACK_HISTORY_DATA

class BasePlayer:
    def __init__(self, player_name: str, personality: str):
        self.player_name = player_name
        self.personality = personality
        self.source_manager = HistorySourceManager()

    def score_candidate(self, record: SourceRecord, identifier: str, suffix: str, reading: str, next_prefixes: List[str]) -> float:
        """人格に応じた候補のスコアリング（派生クラスでオーバーライド）"""
        return 0.0

    def generate_comment(self, chosen: SourceRecord, identifier: str, suffix: str) -> str:
        """思考コメントの生成"""
        return f"{chosen.title} を選択しました。"

    def run(self):
        """標準入力から指示を受け取り、返信を出力するメインループ"""
        try:
            input_text = sys.stdin.read()
            if not input_text.strip():
                self.output_error("empty_input")
                return

            instruction = json.loads(input_text)
        except Exception as e:
            self.output_error(f"invalid_json: {e}")
            return

        reply = self.process_instruction(instruction)
        # 純粋な JSON のみを出力 (P-3 減点防止)
        sys.stdout.write(json.dumps(reply, ensure_ascii=False))
        sys.stdout.flush()

    def process_instruction(self, instruction: Dict[str, Any]) -> Dict[str, Any]:
        req_prefix = instruction.get("required_prefix", "")
        history = instruction.get("history", [])
        used_readings = {h.get("identifier_reading") for h in history if h.get("identifier_reading")}
        game_id = instruction.get("game_id", "")
        turn = instruction.get("turn", 1)
        in_reply_to = instruction.get("message_id", "")

        # 候補検索
        candidates = self.find_candidates(req_prefix, used_readings)

        if not candidates:
            # 回答不能
            reply = TurnReply(
                in_reply_to=in_reply_to,
                game_id=game_id,
                turn=turn,
                from_=self.player_name,
                status="cannot_answer",
                comment="有効な歴史出来事の候補が見つかりませんでした"
            )
            return reply.to_dict()

        # スコアリングして最良の候補を選択
        best_cand = max(candidates, key=lambda c: c["score"])
        rec: SourceRecord = best_cand["record"]
        ident = best_cand["identifier"]
        suffix = best_cand["suffix"]
        reading = best_cand["reading"]
        category = best_cand["category"]

        # 詳細情報抽出
        event_info = self.build_event_info(rec)
        comment = self.generate_comment(rec, ident, suffix)

        reply = TurnReply(
            in_reply_to=in_reply_to,
            game_id=game_id,
            turn=turn,
            from_=self.player_name,
            status="answered",
            answer={
                "full_name": rec.title,
                "identifier": ident,
                "identifier_reading": reading,
                "suffix": suffix,
                "category": category,
                "source_id": rec.source_id,
                "source_title": rec.title
            },
            event_info=event_info,
            comment=comment[:100] if comment else None
        )
        return reply.to_dict()

    def find_candidates(self, req_prefix: str, used_readings: set) -> List[Dict[str, Any]]:
        valid_candidates = []
        seen_titles = set()

        # 1. まずマスターデータから即座に候補を抽出（高速・確実）
        for item in FALLBACK_HISTORY_DATA:
            ident, suffix = split_event_name(item["title"])
            reading = determine_identifier_reading(item["title"], ident, suffix, item["reading"])

            # 条件チェック
            if len(item["title"].replace(" ", "").replace("　", "")) < 4:
                continue
            if len(reading) < 2:
                continue
            if not verify_prefix_match(reading, req_prefix):
                continue
            if reading in used_readings:
                continue

            rec = SourceRecord(
                source_id="wikipedia_ja",
                title=item["title"],
                url=f"https://ja.wikipedia.org/wiki/{item['title']}",
                reading=item["reading"],
                extract=item["extract"],
                types=[item["category"], "日本史"],
                japan_related=True
            )
            # 8.4節の判定を通過するか確認
            if not self.source_manager.is_historical_event(rec):
                continue

            seen_titles.add(rec.title)
            next_prefixes = generate_candidate_prefixes(reading)
            score = self.score_candidate(rec, ident, suffix, reading, next_prefixes)

            valid_candidates.append({
                "record": rec,
                "identifier": ident,
                "suffix": suffix,
                "reading": reading,
                "category": item["category"],
                "next_prefixes": next_prefixes,
                "score": score
            })

        # 2. 外部 Wikipedia API からのリアルタイム検索（バッチ一括取得で高速化）
        if self.source_manager.is_source_available("wikipedia_ja"):
            try:
                wiki_src = self.source_manager.sources["wikipedia_ja"]
                # 人格やプレフィックスに応じた効率的な検索クエリ (1〜2回)
                search_query = f"{req_prefix} 合戦" if "戦い" in self.personality or "歴史" in self.personality else f"{req_prefix}"
                records = wiki_src.search(search_query, limit=15)
                
                # 未取得タイトルを抽出して一括取得
                candidate_titles = [r.title for r in records if r.title not in seen_titles and ":" not in r.title][:15]
                if candidate_titles:
                    batch_details = wiki_src.fetch_many(candidate_titles)
                    for t, detail in batch_details.items():
                        if not detail or not self.source_manager.is_historical_event(detail):
                            continue
                        if detail.title in seen_titles:
                            continue
                        seen_titles.add(detail.title)

                        ident, suffix = split_event_name(detail.title)
                        reading = determine_identifier_reading(detail.title, ident, suffix, detail.reading)

                        # 条件チェック
                        if len(detail.title.replace(" ", "").replace("　", "")) < 4:
                            continue
                        if len(reading) < 2:
                            continue
                        if not verify_prefix_match(reading, req_prefix):
                            continue
                        if reading in used_readings:
                            continue

                        category = SUFFIX_CATEGORIES.get(suffix)
                        if not category:
                            for ct in detail.types:
                                if ct in VALID_CATEGORIES:
                                    category = ct
                                    break
                            if not category:
                                category = "合戦名" if "戦い" in suffix or "合戦" in suffix else "事件名"

                        next_prefixes = generate_candidate_prefixes(reading)
                        score = self.score_candidate(detail, ident, suffix, reading, next_prefixes)

                        valid_candidates.append({
                            "record": detail,
                            "identifier": ident,
                            "suffix": suffix,
                            "reading": reading,
                            "category": category,
                            "next_prefixes": next_prefixes,
                            "score": score
                        })
            except Exception as e:
                self.source_manager.mark_source_failed("wikipedia_ja")

        return valid_candidates

    def build_event_info(self, rec: SourceRecord) -> Dict[str, Any]:
        extract = rec.extract or ""
        # 年代抽出
        era_match = re.search(r"(\d{3,4}年|[一-龠]{2,4}\d{1,2}年|[一-龠]{2,4}元年)", extract)
        era = era_match.group(1) if era_match else "年代不詳"

        # 主要人物抽出（簡易抽出）
        key_persons = []
        for kw in ["織田信長", "豊臣秀吉", "徳川家康", "武田信玄", "上杉謙信", "明智光秀", "西郷隆盛", "坂本龍馬", "勝海舟"]:
            if kw in extract and kw != rec.title:
                key_persons.append(kw)

        summary = extract[:150] + ("…" if len(extract) > 150 else "")

        return {
            "summary": summary,
            "era": era,
            "key_persons": key_persons,
            "significance": f"{rec.title}は日本の歴史上重要な意義を持ちます。",
            "trivia": f"{rec.title}に関する興味深い出来事です。",
            "sources": [rec.url]
        }

    def output_error(self, err_type: str):
        reply = {
            "protocol_version": "1.0",
            "type": "TURN_REPLY",
            "message_id": generate_message_id(),
            "in_reply_to": "",
            "game_id": "",
            "turn": 1,
            "from": self.player_name,
            "to": "Judge",
            "sent_at": current_iso_time(),
            "status": "cannot_answer",
            "comment": f"Error: {err_type}"
        }
        sys.stdout.write(json.dumps(reply, ensure_ascii=False))
        sys.stdout.flush()
