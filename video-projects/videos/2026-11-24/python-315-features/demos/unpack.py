batches = [["a.py", "b.py"], ["c.py"], ["d.py", "e.py"]]

files = [*batch for batch in batches]
print(files)

defaults = {"theme": "dark"}
user = {"font": 14}
merged = {**d for d in (defaults, user)}
print(merged)
