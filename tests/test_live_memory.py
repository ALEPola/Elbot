"""Per-user GPT-Live memory: a tiny capped, persisted note store."""

import json

from elbot.live.memory import MAX_NOTE_LENGTH, MAX_NOTES_PER_USER, UserMemory


def test_remember_and_recall_round_trip():
    memory = UserMemory(None)
    memory.remember(1, "likes reggaeton")
    memory.remember(1, "call them Ace")
    assert memory.recall(1) == ["likes reggaeton", "call them Ace"]


def test_recall_for_unknown_user_is_empty():
    memory = UserMemory(None)
    assert memory.recall(999) == []


def test_notes_are_capped_per_user():
    memory = UserMemory(None)
    for i in range(MAX_NOTES_PER_USER + 5):
        memory.remember(1, f"note {i}")
    notes = memory.recall(1)
    assert len(notes) == MAX_NOTES_PER_USER
    assert notes[0] == "note 5"  # oldest ones evicted first
    assert notes[-1] == f"note {MAX_NOTES_PER_USER + 4}"


def test_notes_are_truncated_to_max_length():
    memory = UserMemory(None)
    memory.remember(1, "x" * (MAX_NOTE_LENGTH + 50))
    assert len(memory.recall(1)[0]) == MAX_NOTE_LENGTH


def test_blank_note_is_ignored():
    memory = UserMemory(None)
    memory.remember(1, "   ")
    assert memory.recall(1) == []


def test_forget_all_clears_only_that_user():
    memory = UserMemory(None)
    memory.remember(1, "about user 1")
    memory.remember(2, "about user 2")
    assert memory.forget_all(1) is True
    assert memory.recall(1) == []
    assert memory.recall(2) == ["about user 2"]


def test_forget_all_on_unknown_user_reports_false():
    memory = UserMemory(None)
    assert memory.forget_all(404) is False


def test_persists_across_instances(tmp_path):
    path = tmp_path / "live_memory.json"
    first = UserMemory(path)
    first.remember(1, "likes reggaeton")

    second = UserMemory(path)
    assert second.recall(1) == ["likes reggaeton"]


def test_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "live_memory.json"
    path.write_text("not json")
    memory = UserMemory(path)
    assert memory.recall(1) == []
    memory.remember(1, "still works")
    assert memory.recall(1) == ["still works"]


def test_ignores_malformed_entries_on_load(tmp_path):
    path = tmp_path / "live_memory.json"
    path.write_text(json.dumps({"1": [{"text": "good"}, {"no_text": True}, "not a dict"], "2": "not a list"}))
    memory = UserMemory(path)
    assert memory.recall(1) == ["good"]
    assert memory.recall(2) == []
