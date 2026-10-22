"""
agent_runner.py - メッセンジャー (4.4節 配送規則 Protocol v1.0)
指示メッセージの配送・返信受信・プロセス管理・タイムアウト制御
"""

import os
import sys
import json
import time
import subprocess
import re
from typing import Tuple, Optional, Dict, Any

# vendor ディレクトリのロード
VENDOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "vendor"))
if os.path.exists(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

LOGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "logs"))
MESSAGES_LOG_FILE = os.path.join(LOGS_DIR, "messages.jsonl")

# エージェント実行スクリプトのマッピング
PLAYER_SCRIPTS = {
    "PlayerA": os.path.abspath(os.path.join(os.path.dirname(__file__), "agents", "player_a.py")),
    "PlayerB": os.path.abspath(os.path.join(os.path.dirname(__file__), "agents", "player_b.py")),
    "PlayerC": os.path.abspath(os.path.join(os.path.dirname(__file__), "agents", "player_c.py")),
}

def log_message(msg: Dict[str, Any]):
    """D-7: 送受信メッセージを logs/messages.jsonl に記録"""
    os.makedirs(LOGS_DIR, exist_ok=True)
    try:
        with open(MESSAGES_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Messenger Error] Failed to log message: {e}", file=sys.stderr)

def extract_first_json(text: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """
    D-5: 返信本文から最初の完全な JSON オブジェクトを抽出。
    Returns: (parsed_json, has_extra_text)
    has_extra_text が True の場合は P-3 ペナルティ対象。
    """
    text_stripped = text.strip()
    if not text_stripped:
        return None, False

    # 最初に { を探す
    start_idx = text_stripped.find('{')
    if start_idx == -1:
        return None, False

    # 括弧のバランスを数えて最初の完全な JSON を探す
    depth = 0
    in_string = False
    escape = False
    end_idx = -1

    for i in range(start_idx, len(text_stripped)):
        ch = text_stripped[i]
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break

    if end_idx == -1:
        return None, False

    json_str = text_stripped[start_idx:end_idx]
    has_extra_text = (start_idx > 0) or (end_idx < len(text_stripped))

    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            return data, has_extra_text
    except Exception:
        pass

    return None, False

def dispatch_instruction(
    player_name: str,
    instruction: Dict[str, Any],
    timeout_sec: float = 60.0
) -> Tuple[Optional[Dict[str, Any]], str, float, bool]:
    """
    指示メッセージを Player プロセスに送信し、返信を受信する。
    Returns: (reply_dict, error_status, elapsed_ms, has_extra_text)
      error_status: "ok", "TIMEOUT" (e-2), "CANNOT_ANSWER" (e-1), "MALFORMED" (e-1), "PROCESS_ERROR" (e-1), "STOP_ABORT"
    """
    script_path = PLAYER_SCRIPTS.get(player_name)
    if not script_path or not os.path.exists(script_path):
        return None, "PROCESS_ERROR", 0.0, False

    # 送信直前メッセージログ (D-7)
    log_message(instruction)

    input_json_str = json.dumps(instruction, ensure_ascii=False)

    # D-2: 単調増加クロックで時間計測
    start_time = time.monotonic()

    # D-3: 子プロセス起動
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    
    proc = subprocess.Popen(
        [sys.executable, script_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )

    stdout = ""
    stderr = ""
    error_status = "ok"
    has_extra_text = False

    try:
        stdout, stderr = proc.communicate(input=input_json_str, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        # D-4: タイムアウト時、プロセスを kill し wait()
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=2.0)
        except Exception:
            pass
        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        return None, "TIMEOUT", elapsed_ms, False
    except Exception as e:
        proc.kill()
        elapsed_ms = (time.monotonic() - start_time) * 1000.0
        return None, "PROCESS_ERROR", elapsed_ms, False

    elapsed_ms = (time.monotonic() - start_time) * 1000.0

    if proc.returncode != 0:
        return None, "PROCESS_ERROR", elapsed_ms, False

    # D-5: JSON抽出
    reply_dict, has_extra = extract_first_json(stdout)
    if not reply_dict:
        return None, "MALFORMED", elapsed_ms, False

    has_extra_text = has_extra

    # D-6: 整合性検証
    if (
        reply_dict.get("type") != "TURN_REPLY" or
        reply_dict.get("in_reply_to") != instruction.get("message_id") or
        reply_dict.get("from") != player_name or
        reply_dict.get("game_id") != instruction.get("game_id") or
        reply_dict.get("turn") != instruction.get("turn")
    ):
        return None, "MALFORMED", elapsed_ms, has_extra_text

    # 受信メッセージログ (D-7)
    log_message(reply_dict)

    status = reply_dict.get("status")
    if status == "cannot_answer":
        return reply_dict, "CANNOT_ANSWER", elapsed_ms, has_extra_text
    elif status == "tool_error":
        return reply_dict, "TOOL_ERROR", elapsed_ms, has_extra_text

    return reply_dict, "ok", elapsed_ms, has_extra_text
