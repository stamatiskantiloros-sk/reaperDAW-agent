#!/usr/bin/env python3
"""Generate small synthetic REAPER 7 .rpp fixture projects.

Writes tracks_50.rpp, tracks_150.rpp and small_routed.rpp into --out.
No audio: items use an EMPTY source. Output is deterministic (seeded
random, GUIDs from uuid5 over a per-file counter), so reruns are
byte-identical. Stdlib only.

Usage:
    python3 scripts/make_fixtures.py [--out DIR]
"""

from __future__ import annotations

import argparse
import random
import uuid
from pathlib import Path

REAPER_VERSION = "7.0/macOS-arm64"
REAPER_TIMESTAMP = 1700000000
SAMPLE_RATE = 48000
SEED = 20240
GUID_NAMESPACE = uuid.UUID("6f1c2a7e-9d4b-4e3a-8c51-2b7d0f3e9a10")
DEFAULT_OUT = "tests/fixtures/generated"

BASE_NAMES = [
    "Kick In",
    "Kick Out",
    "Snare Top",
    "Snare Bot",
    "Hat",
    "OH L",
    "OH R",
    "Bass DI",
    "Bass Amp",
    "Gtr DI",
    "Gtr Amp",
    "Keys L",
    "Keys R",
    "Vox Lead",
    "Vox BGV",
    "Synth Pad",
    "Ref Mix",
]

# Native REAPER colours: 0x1000000 flag plus 0xBBGGRR.
PALETTE = [
    0x1000000 | (r | (g << 8) | (b << 16))
    for (r, g, b) in [
        (0xD5, 0x5E, 0x00),
        (0x00, 0x9E, 0x73),
        (0x56, 0xB4, 0xE9),
        (0xF0, 0xE4, 0x42),
        (0xCC, 0x79, 0xA7),
    ]
]


class Ids:
    """Hands out GUIDs derived from a counter, so they are stable per fixture."""

    def __init__(self, fixture: str) -> None:
        self.fixture = fixture
        self.count = 0

    def guid(self, kind: str) -> str:
        self.count += 1
        value = uuid.uuid5(GUID_NAMESPACE, f"{self.fixture}/{kind}/{self.count}")
        return "{" + str(value).upper() + "}"


def quote(text: str) -> str:
    if '"' in text:
        raise ValueError(f"name must not contain a double quote: {text!r}")
    return f'"{text}"'


def make_tracks(rng: random.Random, count: int) -> list:
    """Realistic mixed names, colours on some, folders and two-take items."""
    tracks = []
    for i in range(count):
        roll = rng.random()
        if roll < 0.2:
            name = f"Audio {i + 1:02d}"
        elif roll < 0.35:
            name = f"Track {i + 1}"
        elif roll < 0.5:
            name = f"Gtr DI_{(i % 20) + 1:02d}"
        else:
            name = rng.choice(BASE_NAMES)
        tracks.append(
            {
                "name": name,
                "colour": rng.choice(PALETTE) if rng.random() < 0.4 else None,
                "folder": None,
                "master": True,
                "aux_from": [],
                "takes": 1,
                "length": rng.choice([2, 4, 8]),
            }
        )

    # Folder groups: a parent, then 3 to 5 children; last child closes it.
    pos = 2
    groups = max(1, count // 20)
    for _ in range(groups):
        size = rng.randint(3, 5)
        if pos + size >= count:
            break
        tracks[pos]["folder"] = "parent"
        for j in range(pos + 1, pos + size):
            tracks[j]["folder"] = "child"
        tracks[pos + size]["folder"] = "last"
        pos += size + 1 + rng.randint(3, 6)

    # A couple of items get two takes.
    for idx in rng.sample(range(count), 2):
        tracks[idx]["takes"] = 2
    return tracks


def track_lines(ids: Ids, t: dict) -> list:
    lines = [f"  <TRACK {ids.guid('track')}"]
    lines.append(f"    NAME {quote(t['name'])}")
    if t["colour"] is not None:
        lines.append(f"    PEAKCOL {t['colour']}")
    if t["folder"] == "parent":
        lines.append("    ISBUS 1 1")
    elif t["folder"] == "last":
        lines.append("    ISBUS 2 -1")
    lines.append(f"    MAINSEND {1 if t['master'] else 0} 0")
    for src in t["aux_from"]:
        lines.append(f"    AUXRECV {src} 0 1 0 0 0 0 0 0 -1 0 0 0")

    lines.append("    <ITEM")
    lines.append("      POSITION 0")
    lines.append("      SNAPOFFS 0")
    lines.append(f"      LENGTH {t['length']}")
    lines.append("      LOOP 1")
    lines.append(f"      NAME {quote(t['name'])}")
    lines.append(f"      GUID {ids.guid('item')}")
    for k in range(t["takes"]):
        selected = " SEL" if k == 0 else ""
        lines.append(f"      TAKE{selected}")
        lines.append(f"        NAME {quote(t['name'] + ('' if k == 0 else ' alt'))}")
        lines.append(f"        GUID {ids.guid('take')}")
        lines.append("        <SOURCE EMPTY")
        lines.append("        >")
    lines.append("    >")
    lines.append("  >")
    return lines


def header_lines() -> list:
    return [
        f'<REAPER_PROJECT 0.1 "{REAPER_VERSION}" {REAPER_TIMESTAMP}',
        "  TEMPO 120 4 4",
        f"  SAMPLERATE {SAMPLE_RATE} 0 0",
    ]


def render(lines: list) -> str:
    return "\n".join(lines) + "\n"


def tracks_project(fixture: str, count: int, seed: int) -> str:
    ids = Ids(fixture)
    rng = random.Random(seed)
    tracks = make_tracks(rng, count)
    lines = header_lines()
    for t in tracks:
        lines.extend(track_lines(ids, t))
    lines.append(">")
    return render(lines)


def small_routed_project() -> str:
    ids = Ids("small_routed")
    names = [
        ("Drums", "parent", True, []),
        ("Kick In", None, True, []),
        ("Snare Top", None, True, []),
        ("OH L", None, True, []),
        ("OH R", "last", True, []),
        ("Bass DI", None, True, []),
        ("Gtr DI_01", None, True, []),
        ("Vox Lead", None, True, []),
        ("Keys L", None, False, []),  # no master send
        ("Verb Bus", None, True, [6, 7]),  # receives from Gtr and Vox
        ("Track 12", None, True, []),
        ("Audio 07", None, True, []),
    ]
    tracks = []
    for i, (name, folder, master, aux) in enumerate(names):
        tracks.append(
            {
                "name": name,
                "colour": PALETTE[i % len(PALETTE)] if i % 3 == 0 else None,
                "folder": folder,
                "master": master,
                "aux_from": aux,
                "takes": 1,
                "length": 8,
            }
        )

    lines = header_lines()
    lines.extend(
        [
            "  <TEMPOENVEX " + ids.guid("tempo"),
            "    ACT 0 -1",
            "    VIS 1 0 1",
            "    ARM 0",
            "    PT 0 120 1",
            "    PT 16 140 1",
            "  >",
            'MARKER 1 0 "chug" 1 0 0 1 R ' + ids.guid("region") + " 0",
            'MARKER 1 8 "" 1',
            'MARKER 2 8 "open" 1 0 0 1 R ' + ids.guid("region") + " 0",
            'MARKER 2 16 "" 1',
            'MARKER 3 16 "chug 2" 1 0 0 1 R ' + ids.guid("region") + " 0",
            'MARKER 3 24 "" 1',
            'MARKER 4 8 "hit" 0 0 1 B ' + ids.guid("marker") + " 0",
        ]
    )
    for t in tracks:
        lines.extend(track_lines(ids, t))
    lines.append(">")
    return render(lines)


def build_all() -> dict:
    return {
        "tracks_50.rpp": tracks_project("tracks_50", 50, SEED + 50),
        "tracks_150.rpp": tracks_project("tracks_150", 150, SEED + 150),
        "small_routed.rpp": small_routed_project(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=DEFAULT_OUT, help="output directory")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in build_all().items():
        # Bytes, not write_text, so newlines are "\n" on every OS.
        (out_dir / filename).write_bytes(text.encode("utf-8"))
        print(f"wrote {out_dir / filename}")


if __name__ == "__main__":
    main()
