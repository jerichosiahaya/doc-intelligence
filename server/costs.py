"""Usage: python costs.py   (summarises logs/requests.jsonl)

Prices are USD per million tokens, set in .env: PRICE_IN / PRICE_OUT for the answer model,
PRICE_VISION_IN / PRICE_VISION_OUT for the vision model. Without them only tokens are shown.
"""
import json
import os
from collections import defaultdict

from dotenv import load_dotenv

from reqlog import PATH

load_dotenv()


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


def price(kind, name):
    v = os.environ.get(f"PRICE_{'VISION_' if kind == 'vision' else ''}{name}")
    return float(v) if v else None


def main():
    if not PATH.exists():
        return print(f"No log at {PATH}. Run ingest.py or ask a question first.")
    rows = [json.loads(line) for line in open(PATH)]
    by = defaultdict(list)
    for r in rows:
        by[(r.get("kind", "ask"), r.get("via", "-"))].append(r)
    total = 0.0
    priced = True
    print(f"{'kind':<8}{'via':<8}{'calls':>6}{'errors':>7}{'p50 s':>7}{'p95 s':>7}{'in tok':>10}{'out tok':>9}{'cost USD':>10}")
    for (kind, via), rs in sorted(by.items()):
        ok = [r for r in rs if r.get("status", "ok") == "ok"]
        tin = sum(r.get("prompt_tokens") or 0 for r in ok)
        tout = sum(r.get("completion_tokens") or 0 for r in ok)
        pin, pout = price(kind, "IN"), price(kind, "OUT")
        cost = (tin * pin + tout * pout) / 1e6 if pin is not None and pout is not None else None
        priced &= cost is not None
        total += cost or 0
        secs = [r["seconds"] for r in ok if "seconds" in r]
        print(f"{kind:<8}{via:<8}{len(rs):>6}{len(rs) - len(ok):>7}{pct(secs, .5):>7.2f}{pct(secs, .95):>7.2f}"
              f"{tin:>10}{tout:>9}{('%.4f' % cost) if cost is not None else '-':>10}")
    answers = [r for r in rows if r.get("kind", "ask") == "ask" and r.get("status") == "ok"]
    if answers:
        print(f"\nAnswers: {len(answers)}, refused {sum(bool(r.get('refused')) for r in answers)}, "
              f"rejected citations {sum(r.get('rejected') or 0 for r in answers)}")
    print(f"Estimated total: {'$%.4f' % total}" if priced else "Set PRICE_IN, PRICE_OUT, PRICE_VISION_IN, PRICE_VISION_OUT in .env for cost.")


if __name__ == "__main__":
    main()
