from trustscore.indexer import block_ranges


def test_block_ranges_cover_span_without_gaps():
    windows = list(block_ranges(1_000, 1_250, 100))
    assert windows == [(1_000, 1_099), (1_100, 1_199), (1_200, 1_250)]


def test_block_ranges_single_block():
    assert list(block_ranges(5, 5, 100)) == [(5, 5)]
