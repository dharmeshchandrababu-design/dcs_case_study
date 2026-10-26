"""Extract step: the time interval, retries on a busy API and a clean stop when credits run out."""
from argparse import Namespace

import pytest

import extract


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body or {}

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def fake_api(monkeypatch, statuses):
    calls = iter(FakeResponse(s, {"tweets": []}) for s in statuses)
    monkeypatch.setattr(extract.requests, "get", lambda *a, **k: next(calls))
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)       # no real waiting in tests


def test_query_contains_interval_and_terms():
    q = extract.build_query({"include_retweets": True}, {"theme": "t", "lang": None, "terms": "#ChargeNow"}, 100, 200)
    assert q == "(#ChargeNow) since_time:100 until_time:200"


def test_query_adds_language_ev_terms_and_retweet_filter():
    cfg = {"include_retweets": False, "ev_terms": {"de": "(E-Auto OR Elektroauto)"}}
    q = extract.build_query(cfg, {"theme": "price", "lang": "de", "terms": "Ladepreis"}, 1, 2)
    assert q == "(Ladepreis (E-Auto OR Elektroauto)) lang:de -filter:retweets since_time:1 until_time:2"


def test_interval_from_inclusive_to_exclusive_in_utc():
    since, until = extract.parse_window(Namespace(date_from="2026-10-01", date_to="2026-10-02"), {})
    assert until - since == 86400
    assert since == 1790812800        # 2026-10-01 00:00 UTC


def test_retries_when_api_busy(monkeypatch):
    fake_api(monkeypatch, [429, 503, 200])
    assert extract.get_page("key", "q", "") == {"tweets": []}


def test_clean_stop_when_credits_run_out(monkeypatch):
    fake_api(monkeypatch, [402])
    with pytest.raises(extract.CreditsExhausted):
        extract.get_page("key", "q", "")


def test_gives_up_after_max_attempts(monkeypatch):
    fake_api(monkeypatch, [500, 500, 500])
    with pytest.raises(RuntimeError):
        extract.get_page("key", "q", "", max_attempts=3)
