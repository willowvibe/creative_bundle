import json


def load_rows(n):
    return [{"id": i, "price": i * 1.07, "tags": ["x"] * 5} for i in range(n)]


def slow_total(rows):
    total = 0
    for row in rows:
        total += float(json.loads(json.dumps(row))["price"])   # needless round trip
    return total


def fast_count(rows):
    return len(rows)


rows = load_rows(200_000)
print(f"rows={fast_count(rows)} total={slow_total(rows):,.0f}")
