"""Government of Canada IP ranges: labelled list + live SSC BGP announcements.

Two sources, merged:

1. config/ranges_gccaedits.json - the public-domain @gccaedits list
   (github.com/ruebot/gccaedits-ip-address-ranges), department-labelled,
   compiled 2014-2016. Labels are historical and may be stale.
2. config/ranges_ssc_bgp.json - prefixes currently announced by Shared
   Services Canada ASNs, refreshed from RIPEstat. These carry no department
   label ("SSC network, department unknown") and fill the gaps left by
   twelve years of network consolidation.

Each IP is labelled by its most specific matching gccaedits range; the
generic SSC BGP label is used only when no gccaedits range covers the IP,
because an unlabelled announcement says nothing about the department.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

# Largest ranges Wikimedia accepts for IP-range contributions
# ($wgRangeContributionsCIDRLimit on WMF wikis).
MAX_V4 = 16
MAX_V6 = 32
# Refuse to explode an IPv6 block into more than this many /32 queries.
MAX_V6_SPLIT = 64

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class Range:
    network: Network
    label: str
    source: str  # "gccaedits" or "ssc-bgp"


def _parse_entry(entry) -> list[Network]:
    """gccaedits entries are 'a.b.c.d/n', ['ip'], or ['start', 'end']."""
    if isinstance(entry, str):
        return [ipaddress.ip_network(entry, strict=False)]
    if len(entry) == 1:
        return [ipaddress.ip_network(entry[0], strict=False)]
    start, end = ipaddress.ip_address(entry[0]), ipaddress.ip_address(entry[1])
    return list(ipaddress.summarize_address_range(start, end))


def load_gccaedits(path: Path = CONFIG / "ranges_gccaedits.json") -> list[Range]:
    data = json.loads(path.read_text())["ranges"]
    out = []
    for label, entries in data.items():
        nets: list[Network] = []
        for e in entries:
            nets.extend(_parse_entry(e))
        for n in ipaddress.collapse_addresses(nets):
            out.append(Range(n, label, "gccaedits"))
    return out


def load_ssc_bgp(path: Path = CONFIG / "ranges_ssc_bgp.json") -> list[Range]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    out = []
    for item in data.get("prefixes", []):
        out.append(Range(ipaddress.ip_network(item["prefix"], strict=False),
                         item["label"], "ssc-bgp"))
    return out


def load_all() -> list[Range]:
    return load_gccaedits() + load_ssc_bgp()


class Labeller:
    """Most-specific-match lookup from IP to range label."""

    def __init__(self, ranges: list[Range]):
        # gccaedits ranges first (most specific first), then SSC BGP prefixes.
        self.ranges = sorted(
            ranges,
            key=lambda r: (0 if r.source == "gccaedits" else 1, -r.network.prefixlen),
        )

    def match(self, ip: str) -> Range | None:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None
        for r in self.ranges:
            if addr.version == r.network.version and addr in r.network:
                return r
        return None


def query_blocks(ranges: list[Range]) -> tuple[list[Network], list[str]]:
    """Union of all ranges, split into blocks Wikimedia will accept.

    Returns (blocks, warnings).
    """
    warnings = []
    v4 = ipaddress.collapse_addresses(r.network for r in ranges if r.network.version == 4)
    v6 = ipaddress.collapse_addresses(r.network for r in ranges if r.network.version == 6)
    blocks: list[Network] = []
    for n in v4:
        blocks.extend([n] if n.prefixlen >= MAX_V4 else n.subnets(new_prefix=MAX_V4))
    for n in v6:
        if n.prefixlen >= MAX_V6:
            blocks.append(n)
        elif 2 ** (MAX_V6 - n.prefixlen) <= MAX_V6_SPLIT:
            blocks.extend(n.subnets(new_prefix=MAX_V6))
        else:
            warnings.append(f"skipped {n}: larger than {MAX_V6_SPLIT} x /{MAX_V6}")
    return blocks, warnings
