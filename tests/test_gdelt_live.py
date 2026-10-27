import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from mbpe.sources.gdelt_bq import BACKFILL_SCHEMA, keywords_sql, match_keywords, normalize
from mbpe.sources.gdelt_live import (
    CATCHUP,
    STEP,
    ChecksumError,
    batch_paths,
    batch_url,
    collect,
    parse_gkg,
    unzip_verified,
)

SAMPLES = [
    ("Bitcoin falls below $80,000", "https://x.com/a"),
    (None, "https://x.com/markets/bitcoin-price-today"),
    ("SEC approves spot bitcoin ETFs", "https://x.com/b"),
    ("SEC fines bank over disclosures", "https://x.com/c"),
    ("Tether mints $1bn of USDT stablecoins", ""),
    ("Federal Reserve holds rates; FOMC statement", "https://x.com/fed"),
    ("US CPI beats forecasts as inflation cools", "https://x.com/d"),
    ("Interest-rate decision looms", "https://x.com/e"),
    ("Cryptography conference opens", "https://x.com/f"),
    ("Untethered: a memoir", "https://x.com/g"),
    ("BTC, crypto and cryptocurrencies rally", "https://x.com/binance-coinbase"),
    ("Weather today", "https://x.com/sec-filings"),
]


def sql_matcher(title, url):
    """Evaluate the generated SQL keyword conditions with Python's re (RE2 subset) on the same text."""
    t = normalize(title, url)
    out = []
    for cond in re.findall(r"IF\((.*?), '(\w+)', NULL\)", keywords_sql()):
        clauses, name = cond
        if all(re.search(p, t) for p in re.findall(r"REGEXP_CONTAINS\(t, r'(.*?)'\)", clauses)):
            out.append(name)
    return out


@pytest.mark.parametrize("title, url", SAMPLES)
def test_python_matcher_identical_to_sql_conditions(title, url):
    assert match_keywords(title, url) == sql_matcher(title, url)


def gkg_line(
    date="20261020120000", url="https://x.com/bitcoin-news", title="Bitcoin jumps", tone="1.5,2,0.5"
):
    f = [""] * 27
    f[0], f[1], f[3], f[4], f[15] = f"{date}-1", date, "x.com", url, tone
    f[26] = f"<PAGE_LINKS></PAGE_LINKS><PAGE_TITLE>{title}</PAGE_TITLE>" if title is not None else ""
    return "\t".join(f)


def zipped(text: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("x.gkg.csv", text)
    return b.getvalue()


def test_parser_schema_equals_backfill_schema():
    raw = "\n".join([gkg_line(), gkg_line(url="https://x.com/weather", title="Sunny"), gkg_line(title=None)])
    df, n_rows = parse_gkg(raw.encode())
    assert df.schema == BACKFILL_SCHEMA
    assert n_rows == 3
    assert df.height == 2  # weather row has no keyword; the untitled row still matches via its URL
    assert df["title"].to_list() == ["Bitcoin jumps", None]
    assert df["keywords"].to_list() == [["bitcoin"], ["bitcoin"]]


def test_parser_skips_translated_and_malformed_rows():
    translated = gkg_line().split("\t")
    translated[25] = "srclc:fra;eng:Moses"
    raw = "\n".join([gkg_line(), "\t".join(translated), "too\tfew\tfields"])
    df, n_rows = parse_gkg(raw.encode())
    assert n_rows == 1 and df.height == 1


def test_md5_and_size_mismatch_rejected():
    blob = zipped(gkg_line())
    good = hashlib.md5(blob).hexdigest()
    assert unzip_verified(blob, size=len(blob), md5=good)
    with pytest.raises(ChecksumError, match="MD5"):
        unzip_verified(blob, size=len(blob), md5="0" * 32)
    with pytest.raises(ChecksumError, match="size"):
        unzip_verified(blob, size=len(blob) + 1, md5=good)


class FakeGdelt:
    """Serves lastupdate.txt and batch files; records requested URLs."""

    def __init__(self, latest: datetime, missing: set[datetime] = frozenset()):
        self.latest, self.missing, self.requested = latest, missing, []

    def blob(self, ts):
        return zipped(gkg_line(date=f"{ts:%Y%m%d%H%M%S}"))

    def __call__(self, url):
        self.requested.append(url)
        if url.endswith("lastupdate.txt"):
            b = self.blob(self.latest)
            body = f"{len(b)} {hashlib.md5(b).hexdigest()} {batch_url(self.latest)}\n".encode()
            return body, {"last-modified": "Tue, 27 Oct 2026 10:01:30 GMT"}  # real server sends lowercase
        ts = datetime.strptime(url.rsplit("/", 1)[1][:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        return None if ts in self.missing else (self.blob(ts), {})


def test_catch_up_skips_saved_batches(tmp_path):
    latest = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    first = FakeGdelt(latest, missing={latest - 3 * STEP})
    s1 = collect(tmp_path, get=first, now=latest + timedelta(minutes=2))
    n_window = int(CATCHUP / STEP)
    assert len(s1["saved"]) == n_window - 1 and s1["not_published"] == [(latest - 3 * STEP).isoformat()]

    meta = json.loads(batch_paths(latest, tmp_path)[1].read_text())
    assert meta["kind"] == "latest" and meta["md5_verified"] and meta["n_rows"] == 1
    assert meta["lastupdate_modified"] == "2026-10-27T10:01:30+00:00"
    saved = pl.read_parquet(batch_paths(latest, tmp_path)[0])
    assert saved.drop("fetched_at").schema == BACKFILL_SCHEMA

    # next run, 15 min later: only the new batch and the still-missing one are requested
    second = FakeGdelt(latest + STEP)
    s2 = collect(tmp_path, get=second, now=latest + STEP + timedelta(minutes=2))
    requested = [u for u in second.requested if not u.endswith("lastupdate.txt")]
    assert requested == [batch_url(latest + STEP), batch_url(latest - 3 * STEP)]
    assert s2["already"] == n_window - 2


def test_batches_older_than_48h_logged_as_gaps(tmp_path):
    start = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    collect(tmp_path, get=FakeGdelt(start), now=start)
    later = start + CATCHUP + 4 * STEP  # machine asleep: the first 4 new batches have left the window
    collect(tmp_path, get=FakeGdelt(later), now=later)
    gaps = json.loads((tmp_path / "gaps.json").read_text())
    assert gaps == [(start + i * STEP).isoformat() for i in range(1, 5)]


def test_availability_uses_batch_label_plus_15_min_not_fetched_at():
    from mbpe.sources.gdelt_bq import news_as_of

    df = pl.DataFrame(
        {
            "DATE": [20261027120000, 20261027121500, 20261027123000],
            # fetched_at deliberately contradicts the label: it must not matter
            "fetched_at": [
                datetime(2026, 10, 27, 13, tzinfo=UTC),
                datetime(2026, 10, 27, 11, tzinfo=UTC),
                None,
            ],
        }
    )
    t = datetime(2026, 10, 27, 12, 30, tzinfo=UTC)
    assert news_as_of(df, t)["DATE"].to_list() == [20261027120000, 20261027121500]  # 12:15 + 15 min <= 12:30
    assert news_as_of(df, t - timedelta(seconds=1))["DATE"].to_list() == [20261027120000]


def test_late_publish_logs_warning(tmp_path, caplog):
    latest = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    gdelt = FakeGdelt(latest)
    on_time = gdelt.__call__

    def late(url):  # lastupdate.txt rewritten 16 min after the batch label
        body, _ = on_time(url)
        return (
            (body, {"last-modified": "Tue, 27 Oct 2026 10:16:00 GMT"})
            if url.endswith("lastupdate.txt")
            else (body, {})
        )

    with caplog.at_level("WARNING"):
        collect(tmp_path, get=late, now=latest + timedelta(minutes=17))
    meta = json.loads(batch_paths(latest, tmp_path)[1].read_text())
    assert meta["late_publish"] and meta["publish_minus_label_s"] == 960
    assert any("availability rule would leak" in r.message for r in caplog.records)

    # the normal case (published 1.5 min after the label in FakeGdelt) is not flagged
    other = tmp_path / "ok"
    collect(other, get=FakeGdelt(latest), now=latest + timedelta(minutes=2))
    assert json.loads(batch_paths(latest, other)[1].read_text())["late_publish"] is False
