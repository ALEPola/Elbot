import os

os.environ["MAFIC_LIBRARY"] = "nextcord"

from elbot.music import MusicQueue, QueuedTrack, TrackHandle


class DummyTrack:
    def __init__(self, title: str, duration: int = 120_000) -> None:
        self.info = {
            "title": title,
            "author": "Tester",
            "length": duration,
            "uri": f"https://example.com/{title}",
            "sourceName": "youtube",
        }


def make_entry(title: str) -> QueuedTrack:
    handle = TrackHandle.from_mafic(DummyTrack(title))
    return QueuedTrack(
        id=title,
        handle=handle,
        query=title,
        channel_id=1,
        requested_by=1,
        requester_display="tester",
    )


def test_queue_add_and_pop():
    queue = MusicQueue()
    track1 = make_entry("a")
    track2 = make_entry("b")
    queue.add(track1)
    queue.add(track2)
    assert len(queue) == 2
    assert queue.pop_next() == track1
    assert queue.pop_next() == track2
    assert queue.pop_next() is None


def test_queue_remove_range_and_shuffle():
    queue = MusicQueue()
    for title in "abcde":
        queue.add(make_entry(title))
    removed = queue.remove_range(1, 3)
    assert [t.id for t in removed] == ["b", "c", "d"]
    assert len(queue) == 2
    queue.shuffle()
    assert {t.id for t in queue.snapshot()} == {"a", "e"}


def test_queue_move_and_replay():
    queue = MusicQueue()
    queue.add(make_entry("a"))
    queue.add(make_entry("b"))
    queue.add(make_entry("c"))
    assert queue.move(2, 0)
    ids = [t.id for t in queue.snapshot()]
    assert ids[0] == "c"
    queue.pop_next()
    replayed = queue.replay_last()
    assert replayed is not None
    assert queue.peek(0).id == replayed.id


def test_queue_remove_index_bounds():
    queue = MusicQueue()
    assert queue.remove_index(0) is None
    queue.add(make_entry("x"))
    assert queue.remove_index(5) is None
    assert queue.remove_index(0).id == "x"
    assert len(queue) == 0
