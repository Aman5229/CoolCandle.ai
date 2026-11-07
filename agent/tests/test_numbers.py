from coolcandle_agent.numbers import invented_numbers, numbers_in


def test_an_invented_price_is_caught() -> None:
    assert invented_numbers("Enter near 88", ['{"entry": "85"}']) == {"88"}


def test_a_sourced_price_passes() -> None:
    assert invented_numbers("Entry 85.50, stop 62", ['{"entry": "85.5", "stop": "62"}']) == set()


def test_indian_grouping_matches_plain_digits() -> None:
    assert invented_numbers("Target ₹1,00,000", ['{"target": "100000.00"}']) == set()


def test_clock_times_and_single_digits_do_not_count() -> None:
    assert numbers_in("3 trades, valid till 10:42") == set()
