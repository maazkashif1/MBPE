import pytest

from mbpe.reporting.generated_tables import BLOCKS, DOCS, MARK, mismatches


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_generated_tables_match_their_sources(doc):
    assert doc.exists(), doc
    bad = mismatches(doc)
    assert not bad, (
        f"{doc}: out-of-date generated tables {bad}; run: uv run python -m mbpe.reporting.generated_tables"
    )


def test_every_registered_table_is_used():
    used = {m.group(1) for doc in DOCS for m in MARK.finditer(doc.read_text())}
    assert set(BLOCKS) <= used, f"registered but not in any document: {sorted(set(BLOCKS) - used)}"


def test_an_edited_number_is_caught(tmp_path):
    doc = DOCS[0].read_text()
    m = next(m for m in MARK.finditer(doc) if m.group(1) == "phase3_step2_tuning")
    tampered = doc[: m.start(2)] + m.group(2).replace("-0.", "+0.", 1) + doc[m.end(2) :]
    path = tmp_path / "PROGRESS.md"
    path.write_text(tampered)
    assert mismatches(path) == ["phase3_step2_tuning"]
