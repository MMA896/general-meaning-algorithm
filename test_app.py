import os
import tempfile
import unittest
from unittest.mock import patch

TEST_DIR = tempfile.mkdtemp(prefix="gma-tests-")
os.environ["COOKIE_SECURE"] = "0"
os.environ["DATABASE_PATH"] = os.path.join(TEST_DIR, "test.sqlite3")
os.environ["INDEX_LOCK_PATH"] = os.path.join(TEST_DIR, "index.lock")
os.environ["INDEX_STATUS_PATH"] = os.path.join(TEST_DIR, "index-status.json")
from app import app, normalize  # noqa: E402


class SearchAppTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def login_session(self):
        with self.client.session_transaction() as session:
            session["authenticated"] = True

    def test_normalization(self):
        self.assertEqual(normalize(" أَنْفُسِكُمْ "), "انفسكم")

    def test_health_has_index_fields_without_starting_network_import(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("version", r.json)
        self.assertIn("index_ready", r.json)
        self.assertIn("import_state", r.json)

    def test_api_requires_login(self):
        r = self.client.post("/api/search", json={"query": "نفس"})
        self.assertEqual(r.status_code, 401)
        self.assertIsInstance(r.json, dict)

    def test_empty_query_after_login(self):
        self.login_session()
        r = self.client.post("/api/search", json={"query": " "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json)

    def test_invalid_mode_is_rejected(self):
        self.login_session()
        r = self.client.post("/api/search", json={"query": "نفس", "mode": "invalid"})
        self.assertEqual(r.status_code, 400)

    def test_indexing_returns_clean_json_503(self):
        self.login_session()
        pending = {"ok": False, "status_code": 503, "code": "INDEXING",
                   "state": "running", "error": "الفهرس قيد التجهيز", "detail": "running"}
        with patch("app.analyze", return_value=pending):
            r = self.client.post("/api/search", json={"query": "نفس"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json["code"], "INDEXING")
        self.assertEqual(r.json["state"], "running")

    def test_non_json_api_body_is_handled(self):
        self.login_session()
        r = self.client.post("/api/search", data="not-json", content_type="text/plain")
        self.assertEqual(r.status_code, 400)
        self.assertIsInstance(r.json, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
