"""Cheap consistency checks on extracted data. Pure functions, no I/O."""
import re

NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def to_number(cell):
    m = NUM.fullmatch((cell or "").strip().rstrip("%").replace(" ", ""))
    return float(m.group().replace(",", "")) if m else None


def numbers(text):
    """Every number in the text, with thousands separators removed."""
    return {n.replace(",", "") for n in NUM.findall(text)}


def missing_numbers(table_text, vision_text):
    """Numbers in the native table that the vision transcript does not contain."""
    return sorted(numbers(table_text) - numbers(vision_text))


def check_totals(rows, tol=0.5):
    """For each row labelled 'Total...', compare each column with the sum of the rows above it.
    Rows above that contain no number in that column are skipped. Returns a list of messages."""
    problems = []
    for i, row in enumerate(rows):
        if not (row[0] or "").strip().lower().startswith("total"):
            continue
        for j in range(1, len(row)):
            total = to_number(row[j])
            parts = [to_number(r[j]) for r in rows[1:i] if j < len(r)]
            parts = [p for p in parts if p is not None]
            if total is None or not parts:
                continue
            if abs(sum(parts) - total) > tol:
                col = (rows[0][j] or "").strip()
                problems.append(f"'{row[0].strip()}' column '{col}': rows add up to {sum(parts):g}, table says {total:g}")
    return problems
