from metricool_sync_posts.build_info import build_label


def test_build_label_env(monkeypatch):
    monkeypatch.setenv("METRICOOL_SYNC_BUILD", "test-sha-abc")
    assert build_label() == "test-sha-abc"


def test_build_label_nonempty_without_env(monkeypatch):
    monkeypatch.delenv("METRICOOL_SYNC_BUILD", raising=False)
    label = build_label()
    assert isinstance(label, str)
    assert len(label) > 0
