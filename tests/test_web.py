import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # web deps not installed (bot-only CI)
    TestClient = None


def edit(i, ip, title, ts, wiki="enwiki", comment="", label="Shared Services Canada"):
    return {"wiki": wiki, "host": "en.wikipedia.org", "revid": i, "parentid": i - 1, "timestamp": ts,
            "title": title, "ns": 0, "comment": comment, "sizediff": 10, "ip": ip,
            "range": "205.193.0.0/16", "label": label, "label_source": "gccaedits",
            "url": f"https://en.wikipedia.org/w/index.php?diff={i}"}


@unittest.skipIf(TestClient is None, "fastapi not installed")
class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        data = root / "data" / "2025"
        data.mkdir(parents=True)
        rows = [
            edit(1, "205.193.239.44", "30th Field Artillery Regiment (Canada)", "2025-07-09T15:00:00Z"),
            edit(2, "205.193.239.76", "30th Field Artillery Regiment (Canada)", "2025-07-09T16:00:00Z"),
            edit(3, "198.103.249.201", "Élections générales québécoises de 2022", "2025-02-05T12:00:00Z",
                 wiki="frwiki", comment="lien"),
            edit(4, "199.212.20.3", "Wainwright, Alberta", "2025-03-01T10:00:00Z",
                 comment="weather station", label="Environment Canada"),
            edit(5, "205.193.1.1", "Q42", "2025-04-01T10:00:00Z", wiki="wikidatawiki"),
        ]
        (data / "edits.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (root / "data" / "topics.json").write_text(json.dumps({
            "pages": {"enwiki|30th Field Artillery Regiment (Canada)": [
                {"topic": "History and Society: Military and warfare", "score": 0.9}]},
            "labels": {"wikidatawiki|Q42": "Douglas Adams (English writer)"}}))
        os.environ["WIKISSC_DATA_DIR"] = str(root / "data")
        os.environ["WIKISSC_DB"] = str(root / "test.db")
        os.environ.pop("WIKISSC_SYNC_URL", None)
        import importlib
        import web.app as appmod
        cls.appmod = importlib.reload(appmod)
        cls.client = TestClient(cls.appmod.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.tmp.cleanup()
        for k in ("WIKISSC_DATA_DIR", "WIKISSC_DB"):
            os.environ.pop(k, None)

    def test_meta(self):
        m = self.client.get("/api/meta").json()
        self.assertEqual(m["edits"], 5)
        self.assertEqual(m["ips"], 5)
        self.assertIn("Canadian military", [b["beat"] for b in m["beats"]])

    def test_search_fts_accents_and_filters(self):
        c = self.client
        self.assertEqual(c.get("/api/search", params={"q": "regiment"}).json()["total"], 2)
        self.assertEqual(c.get("/api/search", params={"q": "elections"}).json()["total"], 1)  # diacritics folded
        self.assertEqual(c.get("/api/search", params={"q": "Douglas"}).json()["total"], 1)    # wikidata label
        self.assertEqual(c.get("/api/search", params={"ip": "205.193.239.0/24"}).json()["total"], 2)
        self.assertEqual(c.get("/api/search", params={"beat": "Canadian military"}).json()["total"], 2)
        self.assertEqual(c.get("/api/search", params={"topic": "History and Society: Military and warfare"}).json()["total"], 2)
        self.assertEqual(c.get("/api/search", params={"from": "2025-07-01", "to": "2025-07-09"}).json()["total"], 2)
        self.assertEqual(c.get("/api/search", params={"q": '"unbalanced'}).status_code, 200)
        r = c.get("/api/search", params={"q": "regiment"}).json()
        self.assertEqual(r["items"][0]["topics"], ["History and Society: Military and warfare"])
        self.assertIn("Canadian military", r["items"][0]["beats"])

    def test_ip_profile_and_neighbours(self):
        p = self.client.get("/api/ips/205.193.239.44").json()
        self.assertEqual(p["edits"], 1)
        self.assertEqual(p["pages"], 1)
        self.assertEqual(p["neighbours"][0]["k"], "205.193.239.76")
        self.assertEqual(self.client.get("/api/ips/not-an-ip").status_code, 400)
        self.assertEqual(self.client.get("/api/ips/8.8.8.8").status_code, 404)

    def test_aggregates(self):
        ips = self.client.get("/api/ips").json()
        self.assertEqual(ips["total"], 5)
        t = self.client.get("/api/topics").json()["items"]
        self.assertEqual(t[0]["k"], "Canadian military")
        d = self.client.get("/api/topics/beat/Canadian military").json()
        self.assertEqual(d["edits"], 2)
        self.assertEqual(len(self.client.get("/api/ranges").json()["items"]), 2)

    def test_export_and_spa(self):
        r = self.client.get("/api/export.csv", params={"beat": "Canadian military"})
        self.assertEqual(len(r.text.strip().splitlines()), 3)
        self.assertIn("text/html", self.client.get("/ips").headers["content-type"])
        self.assertEqual(self.client.get("/api/nope").status_code, 404)

    def test_provenance_file_traversal_blocked(self):
        self.assertEqual(self.client.get("/api/provenance/file/../../etc/passwd").status_code, 404)
        self.assertEqual(self.client.get("/api/provenance/file/data/2025/edits.jsonl").status_code, 404)


if __name__ == "__main__":
    unittest.main()
