from app.digits import EXPECTED_LENGTH, analyze, count_digits, sum_digit_counts


def test_count_digits() -> None:
    counts = count_digits("1123")
    assert counts["1"] == 2
    assert counts["2"] == 1
    assert counts["3"] == 1
    assert counts["0"] == 0


def test_count_digits_empty() -> None:
    counts = count_digits("")
    assert all(value == 0 for value in counts.values())


def test_count_digits_ignores_non_digits() -> None:
    counts = count_digits("1\n2 3")
    assert counts["1"] == 1
    assert counts["2"] == 1
    assert counts["3"] == 1


def test_sum_digit_counts() -> None:
    total = sum_digit_counts([count_digits("11"), count_digits("22")])
    assert total["1"] == 2
    assert total["2"] == 2
    assert total["0"] == 0


def test_analyze_clean_line() -> None:
    result = analyze("1" * EXPECTED_LENGTH)
    assert result["counts"]["1"] == EXPECTED_LENGTH
    assert result["non_digit"] == 0
    assert result["length"] == EXPECTED_LENGTH


def test_analyze_counts_non_digits() -> None:
    result = analyze("12ab")
    assert result["counts"]["1"] == 1
    assert result["non_digit"] == 2
    assert result["length"] == 4


def test_analyze_ignores_trailing_newline() -> None:
    result = analyze("123\n")
    assert result["non_digit"] == 0
    assert result["length"] == 3


def test_analyze_empty() -> None:
    result = analyze("")
    assert result["non_digit"] == 0
    assert result["length"] == 0
