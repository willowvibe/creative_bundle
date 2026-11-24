settings = frozendict(host="localhost", port=8080, debug=False)

cache = {settings: "connection #1"}          # hashable: works as a dict key
print(cache[frozendict(port=8080, host="localhost", debug=False)])

prod = settings | {"host": "db.prod", "debug": False}
print(prod)

settings["port"] = 9000                      # immutable
