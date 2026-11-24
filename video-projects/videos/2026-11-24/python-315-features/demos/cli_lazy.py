import argparse
lazy import asyncio
lazy import email.mime.multipart
lazy import http.server
lazy import json
lazy import decimal
lazy import sqlite3
lazy import xml.dom.minidom

parser = argparse.ArgumentParser(prog="tool")
parser.add_argument("--version", action="store_true")
args = parser.parse_args()
print("tool 1.0")
