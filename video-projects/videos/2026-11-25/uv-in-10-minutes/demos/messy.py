import os
import sys


def greet(name):
    return f"Hello, {name}!"


print(greet(sys.argv[1] if len(sys.argv) > 1 else "world"))
