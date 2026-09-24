"""Tests for fmp_data.py — FMP /stable price + sector-universe helpers.

FMP retired the /api/v3 endpoints for new keys (403 "Legacy Endpoint"), and
the old stable path `historical-price-full` 404s; stock-screener is restricted
on the free plan. All HTTP is faked here.
"""

from __future__ import annotations

import fmp_data


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return _Resp(404, {})

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, json))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        return _Resp(404, {})


FLAT = [
    {"symbol": "AAPL", "date": "2026-09-24", "adjClose": 335.9, "volume": 1},
    {"symbol": "AAPL", "date": "2026-09-23", "adjClose": 337.0, "volume": 1},
]


def test_historical_uses_stable_dividend_adjusted_flat_list():
    s = _Session({"historical-price-eod/dividend-adjusted": _Resp(200, FLAT)})
    data = fmp_data.fetch_raw_historical("AAPL", "k", session=s)
    assert data["symbol"] == "AAPL"
    assert [d["date"] for d in data["historical"]] == ["2026-09-24", "2026-09-23"]
    assert data["historical"][0]["adjClose"] == 335.9
    assert data["historical"][0]["close"] == 335.9  # close mirrors adjClose
    assert "stable/historical-price-eod/dividend-adjusted" in s.calls[0][1]


def test_historical_falls_back_to_legacy_v3_for_legacy_keys():
    s = _Session(
        {
            "historical-price-eod/dividend-adjusted": _Resp(403, {"Error Message": "x"}),
            "api/v3/historical-price-full": _Resp(
                200, {"symbol": "AAPL", "historical": [{"date": "2026-09-24", "adjClose": 1.0}]}
            ),
        }
    )
    data = fmp_data.fetch_raw_historical("AAPL", "k", session=s)
    assert data["historical"][0]["adjClose"] == 1.0


def test_historical_all_fail_returns_none():
    s = _Session({})
    assert fmp_data.fetch_raw_historical("AAPL", "k", session=s) is None


def test_sector_stocks_from_stable_company_screener():
    s = _Session(
        {
            "stable/company-screener": _Resp(
                200,
                [
                    {
                        "symbol": "MSFT",
                        "companyName": "Microsoft",
                        "marketCap": 3e12,
                        "sector": "Technology",
                        "exchangeShortName": "NASDAQ",
                        "isActivelyTrading": True,
                    }
                ],
            )
        }
    )
    stocks = fmp_data.fetch_sector_stocks("Technology", "k", session=s)
    assert stocks[0]["symbol"] == "MSFT"


def test_sector_stocks_fall_back_to_tradingview_scanner_when_restricted():
    # Free FMP plan: company-screener is "Restricted Endpoint".
    s = _Session(
        {
            "stable/company-screener": _Resp(402, "Restricted Endpoint"),
            "scanner.tradingview.com": _Resp(
                200,
                {
                    "data": [
                        {
                            "s": "NASDAQ:NVDA",
                            "d": ["NVDA", "NVIDIA", 5e12, "Electronic Technology", "NASDAQ"],
                        },
                        {
                            "s": "NASDAQ:MSFT",
                            "d": ["MSFT", "Microsoft", 3e12, "Technology Services", "NASDAQ"],
                        },
                    ]
                },
            ),
        }
    )
    stocks = fmp_data.fetch_sector_stocks("Technology", "k", session=s)
    assert [x["symbol"] for x in stocks] == ["NVDA", "MSFT"]
    post = next(c for c in s.calls if c[0] == "POST")
    sectors = next(f["right"] for f in post[2]["filter"] if f["left"] == "sector")
    assert set(sectors) == {"Electronic Technology", "Technology Services"}


def test_unknown_sector_for_scanner_fallback_returns_empty():
    s = _Session({"stable/company-screener": _Resp(402, "Restricted")})
    assert fmp_data.fetch_sector_stocks("Nonsense", "k", session=s) == []
