"""
web_server.py - ホスト（Flask / REST API & UI配信 & Judge スレッド管理）
Game Rule v6.1 / Protocol v1.0 / 3.4節・9.1節
"""

import os
import sys
import json
import threading
from typing import Dict, Any
from flask import Flask, jsonify, request, render_template, send_from_directory

# vendor ディレクトリのロード
VENDOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "vendor"))
if os.path.exists(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

from orchestrator import Orchestrator, GAME_STATE_FILE

app = Flask(
    __name__,
    template_folder=os.path.abspath(os.path.join(os.path.dirname(__file__), "templates")),
    static_folder=os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))
)

orchestrator_instance = Orchestrator()
judge_thread: threading.Thread = None
thread_lock = threading.Lock()

def get_current_state() -> Dict[str, Any]:
    """現在最新のスナップショットを取得"""
    if os.path.exists(GAME_STATE_FILE):
        try:
            with open(GAME_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return orchestrator_instance.state

def judge_thread_worker():
    """Judge オーケストレーターのスレッドワーカー（3.4節, 3.5節）"""
    global judge_thread
    try:
        orchestrator_instance.run_game()
    except Exception as e:
        # 3.5節: Judge スレッドの異常終了時のフォールバック書き込み
        print(f"[Host Fatal] Judge thread unhandled exception: {e}", file=sys.stderr)
        try:
            orchestrator_instance.state["status"] = "ended"
            orchestrator_instance.state["end_reason"] = "INTERNAL_ERROR"
            orchestrator_instance.state["error"] = f"Judge thread exception: {e}"
            orchestrator_instance.save_snapshot()
        except Exception as save_err:
            print(f"[Host Fatal] Failed to record error state: {save_err}", file=sys.stderr)

@app.route("/")
def index():
    """Web UI ダッシュボード配信 (9.2節)"""
    return render_template("index.html", app_version="6.1")

@app.route("/api/state", methods=["GET"])
def api_get_state():
    """最新のスナップショットを返却 (9.1節: 常時 200)"""
    state = get_current_state()
    return jsonify(state), 200

@app.route("/api/start", methods=["POST"])
def api_start():
    """
    ゲーム初期化・開始宣告（Judge を起動）
    受付可能 status: idle / ended
    成功: 202 Accepted, 不可: 409 Conflict
    """
    global judge_thread
    with thread_lock:
        state = get_current_state()
        cur_status = state.get("status", "idle")

        if cur_status not in ["idle", "ended"]:
            return jsonify({
                "error": f"Cannot start game in status: {cur_status}",
                "status": cur_status
            }), 409

        if judge_thread and judge_thread.is_alive():
            return jsonify({
                "error": "Judge thread is already running",
                "status": cur_status
            }), 409

        # Judge をバックグラウンドスレッドで起動
        judge_thread = threading.Thread(target=judge_thread_worker, daemon=True)
        judge_thread.start()

        return jsonify({
            "message": "Game started",
            "status": "starting"
        }), 202

@app.route("/api/stop", methods=["POST"])
def api_stop():
    """
    終了宣告（stop_requested を設定）
    受付可能 status: starting / player_turn / judging
    成功: 202 Accepted, 不可: 409 Conflict
    """
    state = get_current_state()
    cur_status = state.get("status", "idle")

    if cur_status not in ["starting", "player_turn", "judging"]:
        return jsonify({
            "error": f"Cannot stop game in status: {cur_status}",
            "status": cur_status
        }), 409

    orchestrator_instance.request_stop()

    return jsonify({
        "message": "Stop requested",
        "status": cur_status,
        "stop_requested": True
    }), 202

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """
    状態を idle へ初期化
    受付可能 status: idle / ended
    成功: 200 OK, 不可: 409 Conflict
    """
    global judge_thread
    with thread_lock:
        state = get_current_state()
        cur_status = state.get("status", "idle")

        if cur_status not in ["idle", "ended"]:
            return jsonify({
                "error": f"Cannot reset game in status: {cur_status}",
                "status": cur_status
            }), 409

        if judge_thread and judge_thread.is_alive():
            return jsonify({
                "error": "Judge thread is still running",
                "status": cur_status
            }), 409

        orchestrator_instance.reset_state()
        new_state = get_current_state()
        return jsonify({
            "message": "Game reset to idle",
            "state_version": new_state.get("state_version", 0),
            "status": "idle"
        }), 200

if __name__ == "__main__":
    print("=" * 60)
    print("  歴史出来事しりとり AI アリーナ Web Host (Game Rule v6.1)")
    print("  Access dashboard: http://127.0.0.1:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
