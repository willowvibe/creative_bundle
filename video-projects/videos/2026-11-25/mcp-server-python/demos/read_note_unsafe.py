from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("Unsafe notes")
NOTES = Path(__file__).parent / "notes"


@mcp.tool
def read_note(name: str) -> str:
    """Read one note by file name."""
    return (NOTES / name).read_text()  # DON'T: "../.env.demo" walks out of the notes folder


if __name__ == "__main__":
    mcp.run()
