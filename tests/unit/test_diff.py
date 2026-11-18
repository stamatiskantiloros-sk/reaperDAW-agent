"""Diff engine: one test per field kind, ordering and formatting."""

from __future__ import annotations

import copy

from agent.core.diff import diff, format_changes
from agent.core.models import (
    EnvelopeInfo,
    FxInfo,
    Item,
    Marker,
    Send,
    Take,
    TempoPoint,
    Track,
)
from tests.unit.factories import guid, make_snapshot

DRUMS, KICK, SNARE, BASS, VERB = (guid(i) for i in range(1, 6))


def changed(mutate):
    a = make_snapshot()
    b = copy.deepcopy(a)
    mutate(b)
    return diff(a, b)


def tuples(changes):
    return [(c.kind, c.guid, c.field, c.before, c.after) for c in changes]


def test_identical_snapshots_and_agent_ext_are_ignored():
    def edit(s):
        s.tracks[0].agent_ext["reaper_agent:x"] = "1"
        s.project.agent_ext["reaper_agent:y"] = "2"
        s.items[0].agent_ext["reaper_agent:z"] = "3"
        s.items[0].takes[0].agent_ext["reaper_agent:w"] = "4"

    assert changed(lambda s: None) == []
    assert changed(edit) == []


def test_float_noise_is_ignored():
    assert changed(lambda s: setattr(s.items[0], "length", s.items[0].length + 1e-9)) == []


def test_track_added_and_removed():
    new = Track(guid=guid(50), name="New")
    assert tuples(changed(lambda s: s.tracks.append(new))) == [
        ("track", guid(50), "added", None, "New")
    ]
    assert tuples(changed(lambda s: s.tracks.pop())) == [
        ("track", VERB, "removed", "Verb Bus", None)
    ]


def test_track_scalar_fields():
    def edit(s):
        t = s.tracks[3]
        t.name, t.color, t.folder_depth, t.parent = "B", "#000001", 1, DRUMS
        t.lane_mode, t.automation_mode, t.master_send = "free", "read", False
        t.lane_names = ["a"]

    got = {c.field: (c.before, c.after) for c in changed(edit)}
    assert got == {
        "name": ("Bass DI", "B"),
        "color": (None, "#000001"),
        "folder_depth": (0, 1),
        "parent": (None, DRUMS),
        "lane_mode": ("normal", "free"),
        "automation_mode": ("trim_read", "read"),
        "master_send": (True, False),
        "lane_names": ([], ["a"]),
    }


def test_track_sends_fx_envelopes():
    def edit(s):
        s.tracks[0].sends.append(Send(dest=VERB, volume_db=-3.0))
        s.tracks[1].fx.append(FxInfo(guid=guid(102), name="VST: X"))
        s.tracks[1].envelopes[0].point_count = 5
        s.tracks[3].envelopes.append(EnvelopeInfo(kind="track", name="Volume"))

    got = {(c.guid, c.field): c for c in changed(edit)}
    assert set(got) == {(DRUMS, "sends"), (KICK, "fx"), (KICK, "envelopes"), (BASS, "envelopes")}
    assert got[(DRUMS, "sends")].after == [{"dest": VERB, "volume_db": -3.0, "pre_fader": False}]
    assert got[(KICK, "envelopes")].after[0]["point_count"] == 5
    assert got[(KICK, "fx")].before[0]["guid"] == guid(101)


def test_track_order_single_change_for_survivors_only():
    def edit(s):
        s.tracks.insert(0, Track(guid=guid(60), name="Inserted"))
        bass = next(t for t in s.tracks if t.guid == BASS)
        s.tracks.remove(bass)
        s.tracks.insert(1, bass)

    out = changed(edit)
    orders = [c for c in out if c.field == "order"]
    assert len(orders) == 1
    assert guid(60) not in orders[0].before + orders[0].after
    assert orders[0].after[0] == BASS
    assert [c.field for c in out if c.field == "added"] == ["added"]


def test_pure_insert_is_not_an_order_change():
    out = changed(lambda s: s.tracks.insert(2, Track(guid=guid(60), name="N")))
    assert [c.field for c in out] == ["added"]


def test_items():
    def edit(s):
        s.items[0].track, s.items[0].position, s.items[0].length, s.items[0].lane = BASS, 1, 2, 3
        s.items.append(Item(guid=guid(260), track=KICK, position=0, length=1))
        s.items.pop(1)

    got = {(c.guid, c.field): (c.before, c.after) for c in changed(edit)}
    assert got[(guid(201), "track")] == (KICK, BASS)
    assert got[(guid(201), "position")] == (0.0, 1)
    assert got[(guid(201), "length")] == (8.0, 2)
    assert got[(guid(201), "lane")] == (0, 3)
    assert got[(guid(260), "added")] == (None, KICK)
    assert got[(guid(202), "removed")] == (SNARE, None)


def test_takes():
    def edit(s):
        tk = s.items[0].takes[1]
        tk.name, tk.active, tk.offline, tk.source = "T", True, True, "/x.wav"
        s.items[0].takes.append(Take(guid=guid(390), name="new"))
        s.items[1].takes.clear()

    got = {(c.guid, c.field): (c.before, c.after) for c in changed(edit)}
    assert got[(guid(302), "name")] == ("Kick In T2", "T")
    assert got[(guid(302), "active")] == (False, True)
    assert got[(guid(302), "offline")] == (False, True)
    assert got[(guid(302), "source")] == ("", "/x.wav")
    assert got[(guid(390), "added")] == (None, "new")
    assert got[(guid(303), "removed")] == ("Snare Top", None)


def test_markers():
    def edit(s):
        m = s.markers[0]
        m.name, m.position, m.end, m.color = "x", 1.0, 9.0, "#FF0000"
        s.markers.append(Marker(guid=guid(480), number=4, name="new", position=2.0))
        s.markers.pop(2)

    got = {(c.guid, c.field): (c.before, c.after) for c in changed(edit)}
    assert got[(guid(401), "name")] == ("chug", "x")
    assert got[(guid(401), "position")] == (0.0, 1.0)
    assert got[(guid(401), "end")] == (8.0, 9.0)
    assert got[(guid(401), "color")] == (None, "#FF0000")
    assert got[(guid(480), "added")] == (None, "new")
    assert got[(guid(403), "removed")] == ("hit", None)


def test_project_and_tempo():
    def edit(s):
        s.project.name, s.project.path = "b.rpp", "/tmp/b.rpp"
        s.tempo_map.append(TempoPoint(time=20.0, bpm=90.0))

    out = changed(edit)
    assert [(c.kind, c.field) for c in out] == [
        ("project", "name"),
        ("project", "path"),
        ("tempo", "tempo_map"),
    ]
    assert out[2].after[-1] == {"time": 20.0, "bpm": 90.0, "num": 4, "den": 4, "linear": False}


def test_deterministic_ordering():
    def edit(s):
        s.tempo_map[0].bpm = 100.0
        s.project.name = "z"
        s.markers[0].name = "m"
        s.items[0].lane = 1
        s.items[0].takes[0].name = "t"
        s.tracks[4].name = "v"
        s.tracks[0].color = "#000000"
        s.tracks[0].name = "d"

    out = changed(edit)
    assert [(c.kind, c.guid, c.field) for c in out] == [
        ("track", DRUMS, "color"),
        ("track", DRUMS, "name"),
        ("track", VERB, "name"),
        ("item", guid(201), "lane"),
        ("take", guid(301), "name"),
        ("marker", guid(401), "name"),
        ("project", "", "name"),
        ("tempo", "", "tempo_map"),
    ]


def test_format_changes_one_line_each():
    def edit(s):
        s.tracks[0].name = "Drums 2"
        s.tracks.append(Track(guid=guid(50), name="New"))
        s.tracks.pop(1)

    out = changed(edit)
    lines = format_changes(out).splitlines()
    assert len(lines) == len(out) == 3
    assert any("name" in ln and '"Drums"' in ln and '"Drums 2"' in ln for ln in lines)
    assert any(ln.endswith('added "New"') for ln in lines)
    assert any("removed" in ln and "Kick In" in ln for ln in lines)
    assert format_changes([]) == ""
