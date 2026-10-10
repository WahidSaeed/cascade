from app import scheduler
from app.config import settings


def test_scheduler_is_off_unless_enabled(monkeypatch):
    monkeypatch.setattr(settings, "scheduler", False)
    assert scheduler.start() is None
    monkeypatch.setattr(settings, "scheduler", True)
    t = scheduler.start()
    assert t is not None and t.is_alive()
    scheduler.stop()
    t.join(timeout=35)
    assert {name for name, _, _ in scheduler._jobs()} == {"discovery", "drift"}
