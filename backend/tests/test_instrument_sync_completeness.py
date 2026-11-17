"""不完整的交易所响应不得覆盖证券维表或标记为当日完整快照。"""
from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import instrument_sync


def _client(monkeypatch, failed_exchange=None, *, empty=False):
    def fetch(exchange, *, instrument_type):
        assert instrument_type == "stock"
        if exchange == failed_exchange:
            if empty:
                return []
            raise RuntimeError("503 Service Unavailable")
        symbol = {"SH": "600000.SH", "SZ": "000001.SZ", "BJ": "920001.BJ"}[exchange]
        return [{"symbol": symbol, "name": exchange, "exchange": exchange}]

    monkeypatch.setattr(instrument_sync, "_fetch_instruments_via_provider", lambda: None)
    monkeypatch.setattr(instrument_sync, "get_client", lambda: SimpleNamespace(
        exchanges=SimpleNamespace(get_instruments=fetch),
    ))
    monkeypatch.setattr(instrument_sync.tf_client, "current_mode", lambda: "free")


@pytest.mark.parametrize("exchange", ["SH", "SZ", "BJ"])
@pytest.mark.parametrize("empty", [False, True])
def test_incomplete_response_preserves_existing_bytes(tmp_path, monkeypatch, exchange, empty):
    target = tmp_path / "instruments/instruments.parquet"
    target.parent.mkdir()
    pl.DataFrame({"symbol": ["000001.SZ"], "name": ["原名称"],
                  "as_of": [date(2026, 1, 1)]}).write_parquet(target)
    before = target.read_bytes()
    _client(monkeypatch, exchange, empty=empty)

    with pytest.raises(RuntimeError, match=exchange):
        instrument_sync.sync_instruments(tmp_path)

    assert target.read_bytes() == before


@pytest.mark.parametrize("empty", [False, True])
def test_incomplete_cold_start_does_not_publish_partial_table(tmp_path, monkeypatch, empty):
    _client(monkeypatch, "SZ", empty=empty)
    with pytest.raises(RuntimeError, match="SZ"):
        instrument_sync.sync_instruments(tmp_path)
    assert not (tmp_path / "instruments/instruments.parquet").exists()


def test_complete_response_is_published(tmp_path, monkeypatch):
    _client(monkeypatch)
    assert instrument_sync.sync_instruments(tmp_path) == 3
    result = pl.read_parquet(tmp_path / "instruments/instruments.parquet")
    assert set(result["exchange"]) == {"SH", "SZ", "BJ"}
    assert result["as_of"].to_list() == [instrument_sync.cn_today()] * 3
