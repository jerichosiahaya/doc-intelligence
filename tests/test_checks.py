from checks import check_totals, missing_numbers, numbers, to_number


def test_to_number():
    assert to_number("1,240") == 1240
    assert to_number("91%") == 91
    assert to_number("2.40") == 2.4
    assert to_number("n/a") is None
    assert to_number("") is None
    assert to_number(None) is None


def test_numbers_strips_separators():
    assert numbers("Revenue 8,420 and 2.40 g") == {"8420", "2.40"}


def test_missing_numbers():
    table = "| Revenue | 6,910 | 8,420 |"
    assert missing_numbers(table, "Revenue 6910 8420") == []
    assert missing_numbers(table, "Revenue 6910") == ["8420"]


def test_totals_match():
    rows = [["", "TH", "ID", "Total"], ["Diesel", "310", "228", "538"], ["Electric", "132", "44", "176"],
            ["Total", "442", "272", "714"]]
    assert check_totals(rows) == []


def test_totals_mismatch_is_reported():
    rows = [["", "A"], ["x", "1,000"], ["y", "2"], ["Total", "1,003"]]
    (msg,) = check_totals(rows)
    assert "1002" in msg and "1003" in msg and "'A'" in msg


def test_totals_ignore_text_columns_and_tables_without_total():
    assert check_totals([["", "Type"], ["a", "Cold"], ["Total", "x"]]) == []
    assert check_totals([["", "A"], ["x", "1"], ["y", "2"]]) == []
