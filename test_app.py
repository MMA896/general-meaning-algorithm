import os
import tempfile
import unittest

import app as application


class SearchFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        application.app.config.update(TESTING=True, SECRET_KEY="test-only-secret")
        self.original_db = application.DB_PATH
        application.DB_PATH = os.path.join(self.tmp.name, "test.db")
        application.init()
        self.client = application.app.test_client()
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True

    def tearDown(self):
        application.DB_PATH = self.original_db
        self.tmp.cleanup()

    def test_health_endpoint_reports_status(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_search_returns_expected_contract_and_persists_run(self):
        response = self.client.post("/api/search", json={"query": "نفس"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["run_id"])
        self.assertEqual(body["scan"]["normalized"], "نفس")
        self.assertIsInstance(body["scan"]["observations"], list)
        self.assertIsInstance(body["scan"]["discoveries"], list)
        self.assertIn("loaded", body["corpus"])
        conn = application.sqlite3.connect(application.DB_PATH)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        finally:
            conn.close()

    def test_empty_search_is_rejected_as_json(self):
        response = self.client.post("/api/search", json={"query": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_unauthenticated_search_is_not_permitted(self):
        client = application.app.test_client()
        response = client.post("/api/search", json={"query": "نفس"})
        self.assertIn(response.status_code, (302, 401, 403))


if __name__ == "__main__":
    unittest.main()
