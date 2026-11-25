from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP("Safe notes")
NOTES = (Path(__file__).parent / "notes").resolve()


@mcp.tool
def read_note(name: str) -> str:
    """Read one note by file name."""
    path = (NOTES / name).resolve()
    if not path.is_relative_to(NOTES) or path.suffix != ".md":
        raise ToolError("Only .md files inside the notes folder can be read.")
    return path.read_text()


if __name__ == "__main__":
    mcp.run()
