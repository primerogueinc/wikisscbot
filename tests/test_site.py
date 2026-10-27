import json
import tempfile
import unittest
from pathlib import Path

from wikisscbot import site


class SiteTests(unittest.TestCase):
    def test_build_static_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            res = site.build(out)
            index = (out / "index.html").read_text()
            self.assertIn("window.WIKISSC_STATIC = true", index)
            self.assertIn('src="static/engine.js"', index)
            for f in ("static/app.js", "static/engine.js", "static/style.css", ".nojekyll", "404.html"):
                self.assertTrue((out / f).exists(), f)
            derived = json.loads((out / "derived.json").read_text())
            self.assertEqual(len(derived["files"]), res["files"])
            for f in derived["files"]:
                # Copied byte-for-byte so browser hashes match SHA256SUMS.
                self.assertEqual((out / f).read_bytes(), (site.ROOT / f).read_bytes())


if __name__ == "__main__":
    unittest.main()
