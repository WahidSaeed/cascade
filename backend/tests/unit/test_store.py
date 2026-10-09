"""SqlStore: patterns, users/principals, conversations, runtime config."""
from app.schemas import Pattern, PatternStatus
from app.store import store


def test_pattern_roundtrip(fresh_db):
    p = Pattern(name="Unit conversion", status=PatternStatus.approved)
    store().put_pattern(p)
    store().put_pattern(Pattern(name="Other", status=PatternStatus.draft))
    assert store().get_pattern(p.id).name == "Unit conversion"
    assert [x.id for x in store().approved_patterns()] == [p.id]
    assert len(store().list_patterns()) == 2
    p.name = "Renamed"
    store().put_pattern(p)
    assert store().get_pattern(p.id).name == "Renamed"
    store().delete_pattern(p.id)
    assert store().get_pattern(p.id) is None


def test_users_and_principals(fresh_db):
    store().upsert_user("ben", "Ben", "Support lead", persona=True, groups=["support", "managers"])
    store().upsert_user("bg", "Background")
    assert store().get_user("ben")["groups"] == ["support", "managers"] or set(store().get_user("ben")["groups"]) == {"support", "managers"}
    assert store().principals("ben") == ["user:ben", "group:managers", "group:support", "org:*"]
    assert [u["id"] for u in store().list_users(personas_only=True)] == ["ben"]
    store().set_groups("bg", ["hr"])
    assert store().groups_of("bg") == ["hr"]
    store().set_groups("new", [])
    assert store().get_user("new") is not None
    assert store().get_user("missing") is None


def test_conversations(fresh_db):
    cid, cols = store().get_or_create_conversation(None, "ava", ["c1"])
    assert cols == ["c1"]
    same, _ = store().get_or_create_conversation(cid, "ava")
    assert same == cid
    other, _ = store().get_or_create_conversation(cid, "dana")   # another user's id is never reused
    assert other != cid
    _, cols = store().get_or_create_conversation(cid, "ava", ["c2"])
    assert cols == ["c2"]
    store().add_turn(cid, "ava", "user", "hello")
    store().add_turn(cid, "ava", "assistant", "hi")
    assert [t["role"] for t in store().recent_turns(cid)] == ["user", "assistant"]


def test_runtime_config(fresh_db):
    store().set_config("cache_threshold", 0.9)
    store().set_config("cache_threshold", 0.93)
    assert store().config_overrides() == {"cache_threshold": 0.93}
