import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wikisscbot import http, provenance


class FakeResp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config").mkdir()
        (root / "config" / "settings.json").write_text('{"x": 1}')
        (root / "data" / "2025").mkdir(parents=True)
        (root / "data" / "2025" / "edits.jsonl").write_text('{"a": 1}\n')
        self.root = root
        patches = {
            "ROOT": root, "DATA": root / "data", "MANIFESTS": root / "data" / "manifests",
            "CHAIN": root / "data" / "manifests" / "chain.jsonl",
            "SUMS": root / "data" / "SHA256SUMS", "BUILDLOG": root / "data" / "BUILDLOG.md",
        }
        self.patchers = [mock.patch.object(provenance, k, v) for k, v in patches.items()]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.tmp.cleanup()

    def run_pull(self, body=b'{"query": {}}'):
        with mock.patch("urllib.request.urlopen", return_value=FakeResp(body)):
            with provenance.Recorder("backfill") as rec:
                http.get_json("https://en.wikipedia.org/w/api.php", {"a": "b"})
        self.assertIsNone(http.RECORDER)
        return provenance.finalize(rec, {"years": ["2025"]}, [self.root / "data" / "2025"],
                                   {"edits_2025": 1})

    def test_chain_and_verify(self):
        first = self.run_pull()
        # Distinct run ids even within the same second.
        with mock.patch.object(provenance, "now", return_value="2099-01-01T00:00:00Z"):
            second = self.run_pull(b'{"query": {"x": 1}}')
        self.assertEqual((first["seq"], second["seq"]), (0, 1))
        m2 = json.loads(second["manifest"].read_text())
        self.assertEqual(m2["previous_manifest"]["sha256"], first["sha256"])
        self.assertEqual(m2["responses"]["count"], 1)
        self.assertIn("data/2025/edits.jsonl", m2["outputs"])
        self.assertIn("config/settings.json", m2["inputs"])
        log = (self.root / m2["responses"]["log"]).read_text().splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("en.wikipedia.org", json.loads(log[0])["url"])
        self.assertTrue((self.root / "data" / "BUILDLOG.md").read_text().count("| backfill |") == 2)
        self.assertEqual(provenance.verify(root=self.root), [])

    def test_tampering_detected(self):
        first = self.run_pull()
        # Data file altered after the pull
        (self.root / "data" / "2025" / "edits.jsonl").write_text('{"a": 2}\n')
        self.assertTrue(any("hash mismatch" in p for p in provenance.verify(root=self.root)))
        (self.root / "data" / "2025" / "edits.jsonl").write_text('{"a": 1}\n')
        self.assertEqual(provenance.verify(root=self.root), [])
        # Manifest altered
        m = first["manifest"]
        m.write_text(m.read_text().replace('"edits_2025": 1', '"edits_2025": 9'))
        self.assertTrue(any("manifest hash mismatch" in p for p in provenance.verify(root=self.root)))

    def test_missing_manifest_breaks_chain(self):
        first = self.run_pull()
        with mock.patch.object(provenance, "now", return_value="2099-01-01T00:00:00Z"):
            self.run_pull()
        first["manifest"].unlink()
        self.assertTrue(any("manifest missing" in p for p in provenance.verify(root=self.root)))


if __name__ == "__main__":
    unittest.main()
