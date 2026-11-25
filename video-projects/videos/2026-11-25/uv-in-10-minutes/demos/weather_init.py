import requests
from rich.console import Console
from rich.table import Table

CITIES = {"Hyderabad": (17.38, 78.48), "London": (51.51, -0.13), "New York": (40.71, -74.01)}


def main() -> None:
    table = Table(title="Weather right now")
    table.add_column("City")
    table.add_column("Temp (°C)", justify="right")
    for city, (lat, lon) in CITIES.items():
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
        temp = requests.get(url, timeout=10).json()["current"]["temperature_2m"]
        table.add_row(city, f"{temp:.1f}")
    Console().print(table)
