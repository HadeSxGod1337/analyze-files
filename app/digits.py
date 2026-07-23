from typing import TypedDict

DIGITS = "0123456789"
EXPECTED_LENGTH = 500


class FileStats(TypedDict):
    counts: dict[str, int]
    non_digit: int
    length: int


def count_digits(text: str) -> dict[str, int]:
    return {d: text.count(d) for d in DIGITS}


def analyze(text: str) -> FileStats:
    # Хвостовой перевод строки не считаем аномалией: это нормально для выгрузки.
    stripped = text.strip()
    counts = count_digits(stripped)
    return {
        "counts": counts,
        "non_digit": len(stripped) - sum(counts.values()),
        "length": len(stripped),
    }


def sum_digit_counts(counts_list: list[dict[str, int]]) -> dict[str, int]:
    total = dict.fromkeys(DIGITS, 0)
    for counts in counts_list:
        for digit, value in counts.items():
            total[digit] += value
    return total
