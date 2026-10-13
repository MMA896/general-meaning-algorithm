import os, tempfile, unittest
os.environ["COOKIE_SECURE"]="0"
from app import app, normalize

class SearchAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.NamedTemporaryFile(delete=False); self.tmp.close()
        app.config["TESTING"]=True
        self.client=app.test_client()
    def tearDown(self):
        try: os.unlink(self.tmp.name)
        except OSError: pass
    def test_normalization(self):
        self.assertEqual(normalize(" أَنْفُسِكُمْ "), "انفسكم")
    def test_health(self):
        r=self.client.get("/health")
        self.assertEqual(r.status_code,200)
        self.assertIn("version",r.json)
    def test_api_requires_login(self):
        r=self.client.post("/api/search",json={"query":"نفس"})
        self.assertEqual(r.status_code,401)
    def test_empty_query_after_login(self):
        with self.client.session_transaction() as s: s["authenticated"]=True
        r=self.client.post("/api/search",json={"query":" "})
        self.assertEqual(r.status_code,400)

if __name__=="__main__": unittest.main()
