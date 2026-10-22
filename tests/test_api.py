"""
tests/test_api.py - REST API & Web ホスト統合テスト (9.1節)
"""

import unittest
import os
import sys
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_DIR = os.path.join(PROJECT_ROOT, "vendor")
if VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from web_server import app, orchestrator_instance

class TestRestAPI(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        orchestrator_instance.reset_state()

    def tearDown(self):
        orchestrator_instance.reset_state()

    def test_get_state(self):
        """GET /api/state: 常時 200 OK"""
        res = self.client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "idle")
        self.assertEqual(data.get("schema_version"), "6.1")

    def test_start_and_conflict(self):
        """POST /api/start: idle 時 202, 進行中の再startは 409"""
        # 開始
        res = self.client.post("/api/start")
        self.assertEqual(res.status_code, 202)
        
        # 進行中の start は 409
        res_conflict = self.client.post("/api/start")
        self.assertEqual(res_conflict.status_code, 409)

        # 停止宣告
        res_stop = self.client.post("/api/stop")
        self.assertIn(res_stop.status_code, [202, 409])

    def test_reset(self):
        """POST /api/reset: idle/ended 時 200"""
        res = self.client.post("/api/reset")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "idle")

    def test_index_html(self):
        """GET /: 200 OK で index.html が返る"""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("歴史しりとり AI アリーナ".encode("utf-8"), res.data)

if __name__ == "__main__":
    unittest.main()
