"""
orchestrator.py - Judge（審判・指揮者・オーケストレーター）
進行制御・検証パイプライン・採点・スナップショット更新・実況解説 (Game Rule v6.1 / Protocol v1.0)
"""

import os
import sys
import json
import time
import random
import unicodedata
import threading
import uuid
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

# vendor ディレクトリのロード
VENDOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "vendor"))
if os.path.exists(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from shared.schema import (
    make_initial_state, TurnInstruction, TurnReply,
    VALID_CATEGORIES, SUFFIX_CATEGORIES, current_iso_time, generate_message_id
)
from shared.reading import (
    split_event_name, determine_identifier_reading, verify_prefix_match,
    generate_candidate_prefixes
)
from shared.history_sources import HistorySourceManager, SourceRecord

from agent_runner import dispatch_instruction

import threading
import uuid

# 開始文字候補 44文字 (5.2節)
START_CHARS = [
    'あ', 'い', 'う', 'え', 'お',
    'か', 'き', 'く', 'け', 'こ',
    'さ', 'し', 'す', 'せ', 'そ',
    'た', 'ち', 'つ', 'て', 'と',
    'な', 'に', 'ぬ', 'ね', 'の',
    'は', 'ひ', 'ふ', 'へ', 'ほ',
    'ま', 'み', 'む', 'め', 'も',
    'や', 'ゆ', 'よ',
    'ら', 'り', 'る', 'れ', 'ろ',
    'わ'
]

# パラメータ (11章)
TURN_TIMEOUT_SEC = 60
MAX_TURNS = 50
MAX_CHAR_CHANGE = 3
MIN_START_CANDIDATES = 5
MIN_PREFIX_CANDIDATES = 1
MAX_DISPATCH = 200
MAX_REDISPATCH = 1
UI_POLL_INTERVAL_SEC = 2

GAME_STATE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "game_state.json"))
BOARD_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "board.md"))

class JudgeLLM:
    """Judge LLM - 実況・解説・ボーナス評価・最終総括を担当 (2.4節, 6.12節)"""

    def __init__(self, history_manager: HistorySourceManager):
        self.history_manager = history_manager

    def generate_commentary(self, player: str, turn: int, full_name: str, identifier: str, is_valid: bool, error_code: Optional[str] = None) -> str:
        """実況文を生成 (6.12節)"""
        if not is_valid:
            err_desc = {
                "e-1": "回答不能または不正な形式",
                "e-2": "制限時間超過（60秒）",
                "e-3": "既出の識別子読み",
                "e-4": "テーマ外の名称（日本史に非該当または不正分類）",
                "e-5": "実在が確認できない名称",
                "e-6": "ルール違反（頭文字不一致・文字数不足等）"
            }.get(error_code or "", "無効回答")
            return f"第{turn}ターン、{player} の回答は {err_desc} により無効判定となりました！無念の失格です！"

        templates = [
            f"第{turn}ターン、{player} が力強く『{full_name}』を繰り出しました！場内に緊張が走ります！",
            f"第{turn}ターン、{player} は落ち着いた様子で『{full_name}』と回答！見事なしりとり連鎖です！",
            f"第{turn}ターン、{player} の繰り出した『{full_name}』が炸裂！会場からどよめきが起こります！",
            f"第{turn}ターン、{player} による『{full_name}』！歴史の重みを感じさせる素晴らしい一手です！"
        ]
        return random.choice(templates)

    def evaluate_bonus(
        self,
        full_name: str,
        identifier: str,
        suffix: str,
        reading: str,
        category: str,
        record: Optional[SourceRecord],
        next_prefix: str,
        next_prefix_cand_count: int
    ) -> Dict[str, Any]:
        """ボーナス点を厳格に評価 (6.10節)"""
        # 1. 戦略的価値 (0〜2点)
        strat_score = 0
        strat_reason = "標準的な返しやすさです。"
        if next_prefix_cand_count <= 2 or reading.endswith("ん") or len(next_prefix) > 1:
            strat_score = 2
            strat_reason = f"語尾が『{next_prefix}』となり相手の選択肢を大きく狭める強力な布石です。"
        elif next_prefix_cand_count <= 5 or len(identifier) >= 5:
            strat_score = 1
            strat_reason = f"識別子の長さと適度な返しにくさを備えた巧みな一手です。"

        # 2. 教育的価値 (0〜2点)
        edu_score = 0
        edu_reason = "基本的な歴史事項です。"
        if record and record.extract and len(record.extract) >= 150:
            edu_score = 2
            edu_reason = "日本史の教科書的にも極めて重要であり、歴史的意義の記述が充実しています。"
        elif suffix in ["戦い", "合戦", "戦争", "条約", "乱"]:
            edu_score = 1
            edu_reason = "歴史の転換点を象徴する重要な出来事です。"

        # 3. 希少性 (0〜1点)
        rarity_score = 0
        rarity_reason = "一般的な分類です。"
        if category in ["一揆", "組織名", "時代名", "地名"] or suffix in ["一揆", "変"]:
            rarity_score = 1
            rarity_reason = f"比較的出題されにくい分類『{category}』を的確に突きました。"

        total_bonus = strat_score + edu_score + rarity_score
        return {
            "strategy": {"score": strat_score, "reason": strat_reason},
            "education": {"score": edu_score, "reason": edu_reason},
            "rarity": {"score": rarity_score, "reason": rarity_reason},
            "total_bonus": total_bonus
        }

    def generate_explanation(
        self,
        full_name: str,
        identifier: str,
        suffix: str,
        category: str,
        reading: str,
        record: Optional[SourceRecord],
        verified_by: Optional[str]
    ) -> List[Dict[str, str]]:
        """実況・解説パネル用の項目別解説を生成 (6.12節)"""
        source_tag = verified_by or (record.source_id if record else "llm")

        items = []
        # 構造解析
        suffix_str = suffix if suffix else "（なし）"
        items.append({
            "item": "構造解析",
            "text": f"フル表記: {full_name} ／ 識別子: {identifier}（読み: {reading}） ／ 後半呼称: {suffix_str}",
            "source": "llm"
        })
        # 分類と意味
        items.append({
            "item": "分類",
            "text": f"{category}（日本史における歴史的事象）",
            "source": "llm"
        })

        if record and record.extract:
            extract_text = record.extract.strip()
            # 概要
            summary = extract_text[:200] + ("…" if len(extract_text) > 200 else "")
            items.append({
                "item": "概要",
                "text": summary,
                "source": source_tag
            })
            # 歴史的意義
            items.append({
                "item": "歴史的意義",
                "text": f"『{full_name}』は日本史上の重要な節目として広く記録されています。",
                "source": source_tag
            })
            # 豆知識
            items.append({
                "item": "豆知識・トリビア",
                "text": f"識別子『{identifier}』の語尾を起点に次なる連鎖が紡がれます。",
                "source": "llm"
            })
        else:
            items.append({
                "item": "概要",
                "text": f"『{full_name}』に関する歴史的記録です。",
                "source": "llm"
            })

        return items

    def generate_final_summary(self, winners: List[str], final_scores: Dict[str, int], total_turns: int, turns: List[Dict[str, Any]]) -> str:
        """最終総括を生成 (5.4節, 6.12節)"""
        if not winners:
            return f"対戦は総ターン数 {total_turns} で終了しました。有効回答がなく勝者なし（不成立）となりました。"

        winners_str = "、".join(winners)
        score_lines = "、".join([f"{p}: {s}点" for p, s in final_scores.items()])
        event_names = [t.get("answer", {}).get("full_name") for t in turns if t.get("verdict") == "valid"]
        event_names_str = "、".join(event_names[:10]) + (" ほか" if len(event_names) > 10 else "")

        summary = (
            f"【歴史出来事しりとり AI アリーナ 最終結果】\n"
            f"優勝者: {winners_str}（スコア: {score_lines}）\n"
            f"総ターン数: {total_turns} ターン\n"
            f"今大会で学べた主な歴史出来事:\n"
            f"{event_names_str}\n\n"
            f"各AIエージェントの戦略性と歴史の知識が見事に結実した白熱の戦いでした！"
        )
        return summary


class Orchestrator:
    """Judge Controller - ゲーム進行の一元管理 (3.2節)"""

    def __init__(self):
        self.history_manager = HistorySourceManager()
        self.judge_llm = JudgeLLM(self.history_manager)
        self.state: Dict[str, Any] = make_initial_state()
        self._lock = threading.Lock()
        self.load_state_if_exists()

    def log(self, message: str, log_type: str = "info"):
        """ゲームログに記録"""
        now = current_iso_time()
        entry = {"time": now, "type": log_type, "message": message}
        with self._lock:
            self.state["log"].append(entry)
            if len(self.state["log"]) > 100:
                self.state["log"] = self.state["log"][-100:]
        print(f"[{entry['time']}][{log_type.upper()}] {message}")

    def save_snapshot(self):
        """10.1節 スナップショットのアトミック書き込み"""
        with self._lock:
            self.state["state_version"] += 1
            self.state["updated_at"] = current_iso_time()
            data_to_write = self.state.copy()

        temp_file = GAME_STATE_FILE + f".tmp.{uuid.uuid4()}"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data_to_write, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, GAME_STATE_FILE)
        except Exception as e:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            print(f"[Orchestrator Error] Failed to save snapshot: {e}", file=sys.stderr)
            raise e

        # board.md 自動生成
        self.update_board_md()

    def load_state_if_exists(self):
        """起動時のスナップショット読み込み"""
        if os.path.exists(GAME_STATE_FILE):
            try:
                with open(GAME_STATE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.state.update(loaded)
                    # 5.6節: プロセス再起動時に非終端状態なら ended へ更新
                    if self.state.get("status") not in ["idle", "ended"]:
                        self.state["status"] = "ended"
                        self.state["end_reason"] = "INTERNAL_ERROR"
                        self.state["error"] = "process restarted"
                        self.save_snapshot()
            except Exception as e:
                print(f"[Orchestrator Error] Failed to load state: {e}", file=sys.stderr)

    def update_board_md(self):
        """スナップショットから board.md を生成 (10.1節)"""
        try:
            lines = [
                "# 歴史出来事しりとり 盤面ビュー (board.md)",
                f"- **ゲームID**: {self.state.get('game_id') or 'なし'}",
                f"- **状態**: `{self.state.get('status')}`",
                f"- **バージョン**: {self.state.get('state_version')}",
                f"- **現在の手番**: {self.state.get('current_player') or 'なし'}",
                f"- **必要頭文字列**: `{self.state.get('required_prefix') or 'なし'}`",
                f"- **総ターン数**: {self.state.get('total_turns')}",
                "",
                "## スコアボード",
            ]
            scores = self.state.get("scores", {})
            for p, sc in scores.items():
                dq = " [失格]" if self.state.get("disqualified") == p else ""
                lines.append(f"- **{p}**: {sc}点{dq}")

            lines.append("")
            lines.append("## 直近のターン履歴")
            turns = self.state.get("turns", [])
            if not turns:
                lines.append("*(履歴なし)*")
            else:
                for t in turns[-5:]:
                    ans = t.get("answer", {})
                    v = t.get("verdict")
                    sc = t.get("score", {}).get("total", 0)
                    lines.append(f"- ターン{t.get('turn')} [{t.get('player')}]: {ans.get('full_name')} ({ans.get('identifier')}) - {v} ({sc}点)")

            with open(BOARD_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            print(f"[Orchestrator Error] Failed to update board.md: {e}", file=sys.stderr)

    def request_stop(self):
        """Human からの停止要求を設定 (5.5節)"""
        self.state["stop_requested"] = True
        self.log("Human による終了宣告（/api/stop）を受信しました", "info")
        self.save_snapshot()

    def reset_state(self):
        """状態を idle へ初期化 (5.5節)"""
        cur_version = self.state.get("state_version", 0)
        self.state = make_initial_state()
        self.state["state_version"] = cur_version + 1
        self.save_snapshot()
        self.log("ゲーム状態を idle にリセットしました", "info")

    def run_game(self):
        """ゲームループ実行 (5.1〜5.4節)"""
        try:
            # 5.2節 開始シーケンス
            if not self.start_sequence():
                return

            # ターン巡回ループ
            while True:
                # T1: 終了宣告チェック
                if self.state.get("stop_requested"):
                    self.log("ターン開始前に終了宣告を検出しました", "info")
                    self.finalize_game("HUMAN_STOP")
                    return

                # T2: ウォッチドッグチェック
                if self.state.get("dispatch_count", 0) >= MAX_DISPATCH:
                    self.log(f"指示送信総数が上限 {MAX_DISPATCH} に達しました", "warning")
                    self.finalize_game("WATCHDOG")
                    return

                # T3〜T9: ターン実行
                should_continue = self.execute_turn()
                if not should_continue:
                    break

        except Exception as e:
            print(f"[Orchestrator Fatal] Uncaught error: {e}", file=sys.stderr)
            self.state["status"] = "ended"
            self.state["end_reason"] = "INTERNAL_ERROR"
            self.state["error"] = str(e)
            self.save_snapshot()

    def start_sequence(self) -> bool:
        """5.2節 開始シーケンス"""
        game_id = f"g-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        cur_version = self.state.get("state_version", 0)
        self.state = make_initial_state(game_id)
        self.state["state_version"] = cur_version + 1
        self.state["status"] = "starting"
        self.log(f"ゲーム開始処理中... (game_id: {game_id})", "info")
        self.save_snapshot()

        # 開始文字の抽選と候補確認
        start_char = None
        while self.state["char_change_count"] < MAX_CHAR_CHANGE:
            if self.state.get("stop_requested"):
                self.finalize_game("HUMAN_STOP")
                return False

            candidate_char = random.choice(START_CHARS)
            self.log(f"開始文字を抽選: 『{candidate_char}』", "info")

            # 候補確認 (8.5節)
            has_cand = self.history_manager.has_candidates(
                candidate_char,
                min_count=MIN_START_CANDIDATES,
                log_cb=lambda m: self.log(m, "debug")
            )

            if has_cand:
                start_char = candidate_char
                self.state["start_char"] = start_char
                self.log(f"開始文字『{start_char}』の候補数が充足（{MIN_START_CANDIDATES}件以上）を確認しました", "info")
                break
            else:
                self.state["char_change_count"] += 1
                self.log(
                    f"開始文字『{candidate_char}』の候補不足のため変更を宣言（{self.state['char_change_count']}回目）",
                    "warning"
                )
                self.save_snapshot()
                if self.state["char_change_count"] >= MAX_CHAR_CHANGE:
                    self.log(f"開始文字変更が上限 {MAX_CHAR_CHANGE} に達したため強制終了します", "error")
                    self.finalize_game("START_CHAR_EXHAUSTED")
                    return False

        # 手番順決定（乱数で固定）
        players = ["PlayerA", "PlayerB", "PlayerC"]
        random.shuffle(players)
        self.state["player_turn_order"] = players
        self.state["current_player"] = players[0]
        self.state["required_prefix"] = start_char
        self.state["prefix_reason"] = "start"

        self.log(f"手番巡回順が決定: {' → '.join(players)}", "info")
        self.save_snapshot()
        return True

    def execute_turn(self) -> bool:
        """5.3節 ターンシーケンス (T3〜T9)"""
        current_player = self.state["current_player"]
        turn_num = self.state["total_turns"] + 1
        req_prefix = self.state["required_prefix"]
        prefix_reason = self.state.get("prefix_reason")

        # T3: 指示メッセージ作成
        history_list = [
            {
                "turn": t.get("turn"),
                "player": t.get("player"),
                "full_name": t.get("answer", {}).get("full_name"),
                "identifier_reading": t.get("answer", {}).get("identifier_reading")
            }
            for t in self.state.get("turns", [])
            if t.get("verdict") == "valid"
        ]

        instruction = TurnInstruction(
            game_id=self.state["game_id"],
            turn=turn_num,
            from_="Judge",
            to=current_player,
            required_prefix=req_prefix,
            prefix_reason=prefix_reason,
            time_limit_sec=TURN_TIMEOUT_SEC,
            scores=self.state["scores"].copy(),
            history=history_list
        ).to_dict()

        self.state["status"] = "player_turn"
        self.state["pending_instruction"] = instruction
        self.state["dispatch_count"] = self.state.get("dispatch_count", 0) + 1
        self.log(f"第{turn_num}ターン: {current_player} へ指示送信（必要頭文字列: 『{req_prefix}』）", "info")
        self.save_snapshot()

        # T4: Player へ指示送信・待機
        reply_dict, error_status, elapsed_ms, has_extra = dispatch_instruction(
            current_player,
            instruction,
            timeout_sec=TURN_TIMEOUT_SEC
        )

        # T5: 返信待ち中の stop_requested チェック
        if self.state.get("stop_requested"):
            self.log(f"返信待機中に終了宣告を受信したため、第{turn_num}ターンを破棄します", "info")
            self.finalize_game("HUMAN_STOP")
            return False

        self.state["status"] = "judging"
        self.state["last_reply"] = reply_dict
        self.save_snapshot()

        # T6: 検証パイプライン実行 (6.7節)
        verdict, error_code, penalty_applied, penalty_ids, validated_data = self.run_validation_pipeline(
            reply_dict, error_status, has_extra, req_prefix, history_list
        )

        # T7: 採点・実況解説
        turn_score_data = {"base": 0, "bonus": {}, "penalty": 0, "total": 0}
        commentary = ""
        explanation_items = []
        next_prefix = None
        next_prefix_reason = None

        if verdict == "valid":
            full_name = validated_data["full_name"]
            identifier = validated_data["identifier"]
            suffix = validated_data["suffix"]
            reading = validated_data["reading"]
            category = validated_data["category"]
            record = validated_data["record"]
            verified_by = validated_data["verified_by"]

            # 次の必要頭文字列の決定 (6.4節)
            next_prefix, next_prefix_reason = self.determine_next_prefix(reading)

            # 基本点: フル表記文字数 × 1点 (空白除く、「の」も数える)
            base_score = len(full_name.replace(" ", "").replace("　", ""))

            # ボーナス評価 (Judge LLM)
            cand_count = 3  # 参考値
            bonus_data = self.judge_llm.evaluate_bonus(
                full_name, identifier, suffix, reading, category, record, next_prefix, cand_count
            )
            bonus_total = bonus_data.pop("total_bonus", 0)

            # ペナルティ減点 (-1点)
            penalty_score = -1 if penalty_applied else 0

            total_turn_score = base_score + bonus_total + penalty_score
            turn_score_data = {
                "base": base_score,
                "bonus": bonus_data,
                "penalty": penalty_score,
                "total": total_turn_score
            }

            # 累計スコア更新
            self.state["scores"][current_player] = self.state["scores"].get(current_player, 0) + total_turn_score
            self.state["last_identifier_reading"] = reading

            # 実況と解説
            commentary = self.judge_llm.generate_commentary(current_player, turn_num, full_name, identifier, True)
            explanation_items = self.judge_llm.generate_explanation(
                full_name, identifier, suffix, category, reading, record, verified_by
            )
            self.log(f"第{turn_num}ターン: {current_player} の回答『{full_name}』は有効！ (+{total_turn_score}点)", "info")

        else:
            # 無効回答
            self.state["disqualified"] = current_player
            commentary = self.judge_llm.generate_commentary(current_player, turn_num, "", "", False, error_code)
            self.log(f"第{turn_num}ターン: {current_player} は無効回答（{error_code}）により失格！", "warning")

        # T8: ターン記録作成・追加
        turn_record = {
            "turn": turn_num,
            "player": current_player,
            "message_id": reply_dict.get("message_id") if reply_dict else None,
            "required_prefix": req_prefix,
            "answer": validated_data.get("answer") if verdict == "valid" else (reply_dict.get("answer") if reply_dict else None),
            "verdict": verdict,
            "verified_by": validated_data.get("verified_by") if verdict == "valid" else None,
            "error_code": error_code,
            "penalty": {"applied": penalty_applied, "ids": penalty_ids},
            "score": turn_score_data,
            "elapsed_ms": round(elapsed_ms),
            "commentary": commentary,
            "explanation": explanation_items,
            "next_prefix": next_prefix
        }

        self.state["total_turns"] = turn_num
        self.state["turns"].append(turn_record)
        self.save_snapshot()

        # T9: 終了判定 (優先順位: ①無効回答 ②stop_requested ③MAX_TURNS)
        if verdict != "valid":
            self.finalize_game("INVALID_ANSWER")
            return False

        if self.state.get("stop_requested"):
            self.finalize_game("HUMAN_STOP")
            return False

        if self.state["total_turns"] >= MAX_TURNS:
            self.log(f"総ターン数が上限 {MAX_TURNS} に達しました", "info")
            self.finalize_game("MAX_TURNS")
            return False

        # 次ターンの準備
        self.state["required_prefix"] = next_prefix
        self.state["prefix_reason"] = next_prefix_reason

        # 巡回順の次Playerへ
        order = self.state["player_turn_order"]
        cur_idx = order.index(current_player)
        next_player = order[(cur_idx + 1) % len(order)]
        self.state["current_player"] = next_player

        self.save_snapshot()
        return True

    def run_validation_pipeline(
        self,
        reply_dict: Optional[Dict[str, Any]],
        error_status: str,
        has_extra_text: bool,
        required_prefix: str,
        history: List[Dict[str, Any]]
    ) -> Tuple[str, Optional[str], bool, List[str], Dict[str, Any]]:
        """
        6.7節 検証パイプライン (V0〜V7)
        Returns: (verdict, error_code, penalty_applied, penalty_ids, validated_data)
        """
        validated_data = {}
        penalty_ids = []

        # V0: 受信状態
        if error_status == "TIMEOUT":
            return "invalid", "e-2", False, [], {}
        if error_status in ["CANNOT_ANSWER", "MALFORMED", "PROCESS_ERROR"] or not reply_dict:
            return "invalid", "e-1", False, [], {}
        if error_status == "TOOL_ERROR":
            # 5.6節 疎通確認
            pings = self.history_manager.ping_all()
            if any(pings.values()):
                # 1つでも疎通可なら e-1
                return "invalid", "e-1", False, [], {}
            else:
                # 全滅なら強制終了
                self.state["end_reason"] = "INTERNAL_ERROR"
                self.state["error"] = "All history sources unreachable on tool_error"
                return "invalid", "e-1", False, [], {}

        answer = reply_dict.get("answer")
        if not answer or not isinstance(answer, dict):
            return "invalid", "e-1", False, [], {}

        full_name = answer.get("full_name", "")
        if not full_name or not isinstance(full_name, str):
            return "invalid", "e-1", False, [], {}

        full_name = unicodedata.normalize('NFKC', full_name.strip())

        # V1: 日本語表記・4文字以上
        has_japanese = bool(re.search(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", full_name))
        char_count = len(full_name.replace(" ", "").replace("　", ""))
        if not has_japanese or char_count < 4:
            return "invalid", "e-6", False, [], {}

        # V2: 歴史データソースでの実在確認 (8.8節)
        record, verified_by, first_source_healthy = self.history_manager.fetch_with_fallback(
            full_name, log_cb=lambda m: self.log(m, "debug")
        )

        if not record or record.is_disambiguation:
            if first_source_healthy:
                # Wikipedia が正常応答して見つからなければ e-5
                return "invalid", "e-5", False, [], {}
            else:
                # Wikipedia 障害かつ代替ソースでも確認できない場合はシステム障害 (Playerの過失にしない)
                self.log(f"歴史データソース障害のため実在判定不能: {full_name}", "error")
                self.state["end_reason"] = "INTERNAL_ERROR"
                self.state["error"] = f"Sources failed for title: {full_name}"
                return "invalid", "e-5", False, [], {}

        # V3: テーマ適合確認 (8.4節)
        if not self.history_manager.is_historical_event(record):
            return "invalid", "e-4", False, [], {}

        # V4: 識別子分離と読みの確定 (7章)
        is_person = "人物" in record.types or answer.get("category") == "人物名"
        ident, suffix = split_event_name(full_name, is_person=is_person)
        reading = determine_identifier_reading(full_name, ident, suffix, record.reading)

        if len(reading) < 2:
            return "invalid", "e-6", False, [], {}

        # 分類の決定
        category = SUFFIX_CATEGORIES.get(suffix)
        if not category:
            category = answer.get("category") if answer.get("category") in VALID_CATEGORIES else "事件名"

        # V5: 頭文字の照合 (6.4節)
        if not verify_prefix_match(reading, required_prefix):
            return "invalid", "e-6", False, [], {}

        # V6: 既出判定 (6.3節: 識別子読みの完全一致)
        used_readings = {h.get("identifier_reading") for h in history if h.get("identifier_reading")}
        if reading in used_readings:
            return "invalid", "e-3", False, [], {}

        # V7: 軽微な違反の判定 (6.9節 P-1〜P-3)
        # P-1: 自己申告項目の欠落
        for req_field in ["identifier", "identifier_reading", "suffix", "category"]:
            if req_field not in answer or answer[req_field] is None:
                penalty_ids.append("P-1")
                break

        # P-2: event_info 欠落または sources 空
        ev_info = reply_dict.get("event_info")
        if not ev_info or not isinstance(ev_info, dict) or not ev_info.get("sources"):
            penalty_ids.append("P-2")

        # P-3: 返信本文に JSON 以外の文字列混入
        if has_extra_text:
            penalty_ids.append("P-3")

        penalty_applied = len(penalty_ids) > 0

        validated_data = {
            "full_name": full_name,
            "identifier": ident,
            "suffix": suffix,
            "reading": reading,
            "category": category,
            "record": record,
            "verified_by": verified_by,
            "answer": {
                "full_name": full_name,
                "identifier": ident,
                "identifier_reading": reading,
                "suffix": suffix,
                "category": category,
                "source_id": verified_by or record.source_id,
                "source_title": record.title
            }
        }

        return "valid", None, penalty_applied, penalty_ids, validated_data

    def determine_next_prefix(self, reading: str) -> Tuple[str, Optional[str]]:
        """
        6.4節 必要頭文字列の決定
        候補列から has_candidates を満たす最初の候補を採用。最後は無条件採用。
        """
        candidates = generate_candidate_prefixes(reading)
        if not candidates:
            return "あ", "fallback"

        # 最後の候補は無条件採用
        for idx, cand in enumerate(candidates):
            if idx == len(candidates) - 1:
                # 最後の候補
                reason = "n_rule" if reading.endswith("ん") and idx == 0 else ("fallback" if idx > 0 else None)
                return cand, reason

            # has_candidates 評価
            if self.history_manager.has_candidates(cand, min_count=MIN_PREFIX_CANDIDATES):
                reason = "n_rule" if reading.endswith("ん") and idx == 0 else ("fallback" if idx > 0 else None)
                return cand, reason

        return candidates[0], None

    def finalize_game(self, end_reason: str):
        """5.4節 終了シーケンス"""
        self.state["status"] = "finalizing"
        self.state["end_reason"] = end_reason
        self.log(f"ゲーム終了処理中... 終了理由: {end_reason}", "info")
        self.save_snapshot()

        # 6.11節 勝敗決定
        scores = self.state["scores"]
        disqualified = self.state.get("disqualified")
        total_turns = self.state.get("total_turns", 0)

        winners = []
        if total_turns == 0:
            winners = []
        elif end_reason == "INVALID_ANSWER" and disqualified:
            # 失格者を除く最高得点者
            valid_scores = {p: s for p, s in scores.items() if p != disqualified}
            if valid_scores:
                max_s = max(valid_scores.values())
                winners = [p for p, s in valid_scores.items() if s == max_s]
        else:
            # 全員のうち最高得点者
            max_s = max(scores.values())
            winners = [p for p, s in scores.items() if s == max_s]

        self.state["winners"] = winners

        # 最終総括生成
        summary = self.judge_llm.generate_final_summary(
            winners, scores, total_turns, self.state.get("turns", [])
        )
        self.state["final_summary"] = summary
        self.state["status"] = "ended"
        self.save_snapshot()

        winners_disp = "、".join(winners) if winners else "勝者なし"
        self.log(f"ゲーム終了！ 勝者: {winners_disp}", "info")
