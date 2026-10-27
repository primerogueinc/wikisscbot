import io
import ipaddress
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wikisscbot import collect, post, ranges, report
from wikisscbot.http import ApiError


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.ranges = ranges.load_gccaedits()
        self.lab = ranges.Labeller(self.ranges)

    def test_known_labels(self):
        self.assertEqual(self.lab.match("199.212.20.3").label, "Environment Canada")
        self.assertEqual(self.lab.match("205.193.239.76").label, "Shared Services Canada")
        self.assertEqual(self.lab.match("131.137.1.1").label,
                         "Canadian Department of National Defence")
        self.assertIsNone(self.lab.match("8.8.8.8"))
        self.assertIsNone(self.lab.match("~2025-12345-67"))

    def test_department_label_beats_generic_bgp(self):
        net = ipaddress.ip_network("199.212.0.0/16")
        extra = [ranges.Range(net, "SSC bgp", "ssc-bgp"),
                 ranges.Range(ipaddress.ip_network("199.212.20.0/24"), "SSC bgp /24", "ssc-bgp")]
        lab = ranges.Labeller(self.ranges + extra)
        self.assertEqual(lab.match("199.212.20.3").label, "Environment Canada")
        self.assertEqual(lab.match("199.212.99.1").label, "SSC bgp")

    def test_query_blocks_respect_wikimedia_limits(self):
        blocks, warnings = ranges.query_blocks(self.ranges)
        self.assertFalse(warnings)
        self.assertTrue(all(b.prefixlen >= 16 for b in blocks if b.version == 4))
        # DND /14 must be split into /16s, and every labelled range covered.
        self.assertIn(ipaddress.ip_network("131.132.0.0/16"), blocks)
        for r in self.ranges:
            self.assertTrue(any(r.network.subnet_of(b) or b.subnet_of(r.network)
                                for b in blocks if b.version == r.network.version), r)

    def test_ipv6_split_and_skip(self):
        rs = [ranges.Range(ipaddress.ip_network("2001:db8::/30"), "a", "ssc-bgp"),
              ranges.Range(ipaddress.ip_network("2a00::/16"), "b", "ssc-bgp")]
        blocks, warnings = ranges.query_blocks(rs)
        self.assertEqual(len(blocks), 4)
        self.assertEqual(len(warnings), 1)


def page(contribs, cont=None):
    d = {"batchcomplete": True, "query": {"usercontribs": contribs}}
    if cont:
        d["continue"] = cont
    return d


def contrib(revid, user, ts="2025-03-01T12:00:00Z", title="Ottawa"):
    return {"user": user, "revid": revid, "parentid": revid - 1, "timestamp": ts,
            "title": title, "ns": 0, "comment": "fix", "size": 100, "sizediff": 5}


class CollectTests(unittest.TestCase):
    wiki = {"id": "enwiki", "host": "en.wikipedia.org", "lang": "en"}

    def test_pagination_dedupe_and_temp_accounts(self):
        lab = ranges.Labeller(ranges.load_gccaedits())
        calls = []

        def fake(url, params):
            calls.append(dict(params))
            if "uccontinue" not in params:
                return page([contrib(10, "199.212.20.3", "2025-05-01T00:00:00Z"),
                             contrib(11, "~2025-11111-11")],
                            {"uccontinue": "x", "continue": "-||"})
            return page([contrib(9, "199.212.20.9", "2025-02-01T00:00:00Z"),
                         contrib(10, "199.212.20.3")])

        with mock.patch.object(collect, "get_json", side_effect=fake):
            res = collect.scan_wiki(self.wiki, [ipaddress.ip_network("199.212.20.0/24")],
                                    lab, "2025-12-31T23:59:59Z", "2025-01-01T00:00:00Z")
        self.assertEqual([e["revid"] for e in res.edits], [9, 10])
        self.assertEqual(res.edits[0]["label"], "Environment Canada")
        self.assertEqual(res.edits[1]["url"],
                         "https://en.wikipedia.org/w/index.php?diff=10&oldid=9")
        self.assertEqual(calls[0]["uciprange"], "199.212.20.0/24")
        self.assertEqual(calls[0]["ucend"], "2025-01-01T00:00:00Z")
        self.assertEqual(len(calls), 2)

    def test_aborts_when_wiki_rejects_parameter(self):
        lab = ranges.Labeller([])
        blocks = [ipaddress.ip_network(f"10.{i}.0.0/16") for i in range(10)]
        err = {"error": {"code": "badparams", "info": "uciprange not allowed"}}
        with mock.patch.object(collect, "get_json", return_value=err) as g:
            res = collect.scan_wiki(self.wiki, blocks, lab, "b", "a",
                                    log=io.StringIO())
        self.assertIsNotNone(res.fatal)
        self.assertEqual(g.call_count, 3)

    def test_transient_block_error_continues(self):
        lab = ranges.Labeller([])
        blocks = [ipaddress.ip_network("10.0.0.0/16"), ipaddress.ip_network("10.1.0.0/16")]
        seq = [ApiError("HTTP 502"), page([contrib(5, "10.1.2.3")])]

        def fake(url, params):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch.object(collect, "get_json", side_effect=fake):
            res = collect.scan_wiki(self.wiki, blocks, lab, "b", "a",
                                    log=io.StringIO())
        self.assertIsNone(res.fatal)
        self.assertEqual(len(res.edits), 1)
        self.assertIn("10.0.0.0/16", res.block_errors)
        self.assertEqual(res.edits[0]["label"], "Government of Canada (unlabelled)")


class ReportTests(unittest.TestCase):
    def test_build_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wikisscbot").mkdir()
            src = Path(report.__file__).with_name("template.html")
            (root / "wikisscbot" / "template.html").write_text(src.read_text())
            raw = root / "data" / "2025" / "raw"
            edits = [
                {"wiki": "enwiki", "revid": 2, "timestamp": "2025-06-02T00:00:00Z",
                 "title": "A </script><b>x", "label": "Environment Canada",
                 "ip": "199.212.20.3", "sizediff": -4, "comment": "c", "url": "u"},
                {"wiki": "frwiki", "revid": 1, "timestamp": "2025-01-02T00:00:00Z",
                 "title": "B", "label": "Shared Services Canada", "ip": "205.193.1.1",
                 "sizediff": 3, "comment": "", "url": "u"},
            ]
            report.write_jsonl(edits[:1], raw / "enwiki.jsonl")
            report.write_jsonl(edits[1:], raw / "frwiki.jsonl")
            with mock.patch.object(report, "ROOT", root):
                s = report.build("2025")
            self.assertEqual(s["total_edits"], 2)
            self.assertEqual(s["by_month"], {"2025-01": 1, "2025-06": 1})
            self.assertEqual(s["last_ip_attributed_edit_by_wiki"]["enwiki"],
                             "2025-06-02T00:00:00Z")
            html = (root / "docs" / "index.html").read_text()
            self.assertNotIn("</script><b>", html)
            payload = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
            self.assertEqual(len(json.loads(payload)["edits"]), 2)
            lines = (root / "data" / "2025" / "edits.csv").read_text().splitlines()
            self.assertEqual(len(lines), 3)


class PostTests(unittest.TestCase):
    def test_format(self):
        e = {"wiki": "frwiki", "title": "Québec", "label": "Environment Canada", "url": "U"}
        self.assertTrue(post.format_status(e).startswith("« Québec »"))
        e["wiki"] = "enwiki"
        e["title"] = "x" * 300
        self.assertLess(len(post.format_status(e)), 300)

    def test_disabled_without_env(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(post.enabled())


if __name__ == "__main__":
    unittest.main()
