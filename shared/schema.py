"""
shared/schema.py - 歴史出来事しりとり プロトコル・状態スキーマ定義 (Protocol v1.0 / Game Rule v6.1)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import uuid

PROTOCOL_VERSION = "1.0"
SCHEMA_VERSION = "6.1"

# 11種の許可分類
VALID_CATEGORIES = [
    "合戦名", "戦い名", "乱", "事件名", "戦争名",
    "一揆", "人物名", "地名", "時代名", "条約名", "組織名"
]

# 後半呼称リスト（長い順）
SUFFIXES = ["事変", "事件", "合戦", "戦争", "条約", "戦い", "一揆", "変", "乱"]

# 後半呼称と読みの対応
SUFFIX_READINGS = {
    "事変": "じへん",
    "事件": "じけん",
    "合戦": "かっせん",
    "戦争": "せんそう",
    "条約": "じょうやく",
    "戦い": "たたかい",
    "一揆": "いっき",
    "変": "へん",
    "乱": "らん",
}

# 後半呼称から分類への対応
SUFFIX_CATEGORIES = {
    "事変": "事件名",
    "事件": "事件名",
    "合戦": "合戦名",
    "戦争": "戦争名",
    "条約": "条約名",
    "戦い": "戦い名",
    "一揆": "一揆",
    "変": "事件名",
    "乱": "乱",
}

def current_iso_time() -> str:
    """ISO 8601 形式のタイムスタンプ（タイムゾーン付き）を生成"""
    return datetime.now(timezone.utc).astimezone().isoformat()

def generate_message_id() -> str:
    """メッセージ固有IDを生成"""
    return str(uuid.uuid4())

@dataclass
class AnswerData:
    full_name: str
    identifier: str = ""
    identifier_reading: str = ""
    suffix: str = ""
    category: str = ""
    source_id: str = "wikipedia_ja"
    source_title: str = ""

@dataclass
class EventInfo:
    summary: str = ""
    era: str = ""
    key_persons: List[str] = field(default_factory=list)
    significance: str = ""
    trivia: str = ""
    sources: List[str] = field(default_factory=list)

@dataclass
class TurnInstruction:
    protocol_version: str = PROTOCOL_VERSION
    type: str = "TURN_INSTRUCTION"
    message_id: str = field(default_factory=generate_message_id)
    game_id: str = ""
    turn: int = 1
    from_: str = "Judge"
    to: str = ""
    sent_at: str = field(default_factory=current_iso_time)
    required_prefix: str = ""
    prefix_reason: Optional[str] = None  # None, "n_rule", "fallback", "start"
    time_limit_sec: int = 60
    scores: Dict[str, int] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

@dataclass
class TurnReply:
    protocol_version: str = PROTOCOL_VERSION
    type: str = "TURN_REPLY"
    message_id: str = field(default_factory=generate_message_id)
    in_reply_to: str = ""
    game_id: str = ""
    turn: int = 1
    from_: str = ""
    to: str = "Judge"
    sent_at: str = field(default_factory=current_iso_time)
    status: str = "answered"  # answered, cannot_answer, tool_error
    answer: Optional[Dict[str, Any]] = None
    event_info: Optional[Dict[str, Any]] = None
    comment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["from"] = d.pop("from_")
        return d

def make_initial_state(game_id: Optional[str] = None) -> Dict[str, Any]:
    """10.2節に基づく game_state.json の初期スキーマ"""
    return {
        "schema_version": SCHEMA_VERSION,
        "game_id": game_id,
        "state_version": 0,
        "status": "idle",
        "end_reason": None,
        "stop_requested": False,
        "current_player": None,
        "player_turn_order": [],
        "start_char": None,
        "char_change_count": 0,
        "required_prefix": None,
        "prefix_reason": None,
        "last_identifier_reading": None,
        "total_turns": 0,
        "dispatch_count": 0,
        "scores": {"PlayerA": 0, "PlayerB": 0, "PlayerC": 0},
        "disqualified": None,
        "winners": [],
        "pending_instruction": None,
        "last_reply": None,
        "turns": [],
        "log": [],
        "final_summary": None,
        "error": None,
        "updated_at": current_iso_time()
    }
