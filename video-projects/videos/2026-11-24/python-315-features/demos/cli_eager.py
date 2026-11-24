import argparse
import asyncio
import email.mime.multipart
import http.server
import json
import decimal
import sqlite3
import xml.dom.minidom

parser = argparse.ArgumentParser(prog="tool")
parser.add_argument("--version", action="store_true")
args = parser.parse_args()
print("tool 1.0")
