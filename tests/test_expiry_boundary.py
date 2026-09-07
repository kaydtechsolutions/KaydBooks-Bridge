from qbwc_kit.session import SessionStore


def test_expiry_at_exact_deadline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("qbwc_kit.session.time.monotonic", lambda: clock[0])
    evicted = []
    store = SessionStore(ttl_seconds=5, on_evict=evicted.append)
    session = store.create("synthetic-user", [])
    clock[0] = 104.999
    assert store.prune() == 0
    clock[0] = 105.0
    assert store.prune() == 1
    assert evicted == [session]
    assert store.prune() == 0
