from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("My Notes")
NOTES = Path(__file__).parent / "notes"


@mcp.tool
def search_notes(query: str, limit: int = 5) -> list[dict]:
    """Search my markdown notes for a word or phrase. Returns matching lines with the file name."""
    hits = []
    for file in sorted(NOTES.glob("*.md")):
        for line in file.read_text().splitlines():
            if query.lower() in line.lower():
                hits.append({"file": file.name, "line": line.strip("- ").strip()})
    return hits[:limit]


@mcp.tool
def convert_units(value: float, unit: str) -> str:
    """Convert km<->miles, kg<->lb or C<->F. unit is the unit of the value: km, mi, kg, lb, c or f."""
    table = {"km": (0.621371, "mi"), "mi": (1.609344, "km"), "kg": (2.204623, "lb"), "lb": (0.453592, "kg")}
    unit = unit.lower()
    if unit == "c":
        return f"{value} °C = {value * 9 / 5 + 32:.1f} °F"
    if unit == "f":
        return f"{value} °F = {(value - 32) * 5 / 9:.1f} °C"
    factor, to = table[unit]
    return f"{value} {unit} = {value * factor:.1f} {to}"


if __name__ == "__main__":
    mcp.run()
