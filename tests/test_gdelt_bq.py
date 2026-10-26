import re
from datetime import date

import pytest

from mbpe.sources.gdelt_bq import KEYWORDS, build_sql, match_keywords, year_ranges


@pytest.mark.parametrize(
    "title, url, expected",
    [
        ("Bitcoin falls below $80,000", "https://x.com/a", {"bitcoin"}),
        ("", "https://x.com/markets/bitcoin-price-today", {"bitcoin"}),
        ("BTC rallies as ETF inflows grow", "", {"btc"}),
        ("Crypto markets slide", "", {"crypto"}),
        ("Cryptocurrencies rebound", "", {"cryptocurrency"}),
        ("Tether mints $1bn of USDT stablecoins", "", {"tether", "stablecoin"}),
        ("Binance and Coinbase volumes", "", {"binance", "coinbase"}),
        ("SEC approves spot bitcoin ETFs", "", {"bitcoin", "spot_etf", "sec_crypto"}),
        ("Federal Reserve holds rates", "https://x.com/fomc-statement", {"federal_reserve", "fomc"}),
        ("US CPI beats forecasts as inflation cools", "", {"cpi", "inflation"}),
        ("Interest-rate decision looms", "", {"interest_rate"}),
        ("SEC sues crypto exchange", "", {"crypto", "sec_crypto"}),
        # SEC alone doesn't count
        ("SEC fines bank over disclosures", "", set()),
        # whole words only
        ("Cryptography conference opens", "", set()),
        ("Untethered: a memoir", "", set()),
        ("Bitcoinist launches newsletter", "", set()),
        ("Second quarter results", "https://x.com/sec-filings", set()),
    ],
)
def test_keyword_rule(title, url, expected):
    assert set(match_keywords(title, url)) == expected


def test_sql_uses_every_keyword_pattern_verbatim():
    sql = build_sql(date(2021, 1, 1), date(2022, 1, 1), include_themes=False)
    for name, patterns in KEYWORDS.items():
        assert f"'{name}'" in sql
        for p in patterns:
            assert f"REGEXP_CONTAINS(t, r'{p}')" in sql


def test_sql_scans_only_needed_columns_and_partitions():
    sql = build_sql(date(2021, 1, 1), date(2022, 1, 1), include_themes=False)
    assert "_PARTITIONTIME >= TIMESTAMP('2020-12-31')" in sql
    assert "_PARTITIONTIME < TIMESTAMP('2022-01-02')" in sql
    assert "DATE >= 20210101000000 AND DATE < 20220101000000" in sql
    # Extras is only read to extract the title, never returned whole
    assert re.findall(r"\bExtras\b", sql) == ["Extras"]
    assert "REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>')" in sql
    assert "V2Themes" not in sql
    assert "V2Themes" in build_sql(date(2021, 1, 1), date(2022, 1, 1), include_themes=True)


def test_year_ranges_cover_start_to_today_without_overlap():
    ranges = year_ranges(date(2026, 10, 26))
    assert ranges[0] == (2020, date(2020, 9, 1), date(2021, 1, 1))
    assert ranges[-1] == (2026, date(2026, 1, 1), date(2026, 10, 26))
    assert [y for y, _, _ in ranges] == list(range(2020, 2027))
    assert all(a[2] == b[1] for a, b in zip(ranges, ranges[1:], strict=False))
