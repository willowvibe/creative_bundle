import sqlite3
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP("Shop DB")
DB = Path(__file__).parent / "shop" / "shop.db"


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)  # opened read-only: writes fail in SQLite itself


@mcp.tool
def list_tables() -> dict[str, list[str]]:
    """List every table in the shop database with its columns. Call this first to learn the schema."""
    with connect() as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {t: [col[1] for col in db.execute(f"PRAGMA table_info({t})")] for t in tables}


@mcp.tool
def run_query(sql: str) -> list[dict]:
    """Run one read-only SQL SELECT on the shop database. Returns up to 50 rows."""
    if not sql.lstrip().lower().startswith(("select", "with")):
        raise ToolError("Only SELECT queries are allowed.")
    with connect() as db:
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute(sql).fetchmany(50)]
        except sqlite3.Error as e:
            raise ToolError(f"SQL error: {e}")


if __name__ == "__main__":
    mcp.run()
