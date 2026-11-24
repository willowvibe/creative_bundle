MISSING = sentinel("MISSING")

def get_setting(name, default=MISSING):
    if default is MISSING:
        return f"{name}: no default given"
    return f"{name}: {default!r}"

print(get_setting("timeout"))
print(get_setting("timeout", None))
print(MISSING)
