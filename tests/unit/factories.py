"""Shared test data builders (also used by the fake bridge tests)."""

from __future__ import annotations

from agent.core.models import (
    EnvelopeInfo,
    FxInfo,
    Item,
    Marker,
    ProjectInfo,
    Send,
    Snapshot,
    Take,
    TempoPoint,
    Track,
)


def guid(n: int) -> str:
    """Deterministic well-formed GUID: guid(1) -> {00000000-0000-0000-0000-000000000001}."""
    return f"{{00000000-0000-0000-0000-{n:012X}}}"


def make_snapshot() -> Snapshot:
    """A small project: a drum folder (2 children), a bass track, a reverb bus, items, regions."""
    drums, kick, snare, bass, verb = (guid(i) for i in range(1, 6))
    return Snapshot(
        project=ProjectInfo(
            name="demo.rpp",
            path="/tmp/demo.rpp",
            sample_rate=48000,
            reaper_version="7.86",
            length=24.0,
        ),
        tempo_map=[TempoPoint(time=0.0, bpm=120.0), TempoPoint(time=16.0, bpm=140.0)],
        tracks=[
            Track(guid=drums, name="Drums", color="#0072B2", folder_depth=1),
            Track(
                guid=kick,
                name="Kick In",
                parent=drums,
                fx=[FxInfo(guid=guid(101), name="VST: ReaEQ (Cockos)", param_count=19)],
                envelopes=[
                    EnvelopeInfo(
                        kind="fx", name="Gain-Low Shelf", fx=guid(101), param=1, point_count=3
                    )
                ],
            ),
            Track(
                guid=snare,
                name="Snare Top",
                parent=drums,
                folder_depth=-1,
                sends=[Send(dest=verb, volume_db=-6.0)],
            ),
            Track(
                guid=bass,
                name="Bass DI",
                color=None,
                agent_ext={"reaper_agent:orig_name": "Audio 07"},
            ),
            Track(guid=verb, name="Verb Bus", master_send=True),
        ],
        items=[
            Item(
                guid=guid(201),
                track=kick,
                position=0.0,
                length=8.0,
                takes=[
                    Take(guid=guid(301), name="Kick In", active=True),
                    Take(guid=guid(302), name="Kick In T2"),
                ],
            ),
            Item(
                guid=guid(202),
                track=snare,
                position=0.0,
                length=8.0,
                takes=[Take(guid=guid(303), name="Snare Top", active=True)],
            ),
            Item(
                guid=guid(203),
                track=bass,
                position=8.0,
                length=8.0,
                takes=[Take(guid=guid(304), name="Bass DI", source="/tmp/bass.wav", active=True)],
            ),
        ],
        markers=[
            Marker(guid=guid(401), number=1, name="chug", position=0.0, end=8.0),
            Marker(guid=guid(402), number=2, name="open", position=8.0, end=16.0),
            Marker(guid=guid(403), number=3, name="hit", position=8.0),
        ],
    )
