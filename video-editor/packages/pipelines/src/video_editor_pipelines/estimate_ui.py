"""Terminal confirmation for cost estimates — no Typer in the library layer.

The estimate functions need to show a table and ask a yes/no question, but
``packages/pipelines`` must not import the CLI toolkit (#307). These two
helpers are the whole UI surface; the CLI catches the exceptions below and maps
them to its own exit codes.
"""

from __future__ import annotations


class EstimateError(ValueError):
    """A build cannot be priced (an unpriced provider/model)."""


class EstimateAbortedError(Exception):
    """The operator declined the estimate; the caller should exit without work."""


def echo(message: str) -> None:
    print(message)  # noqa: T201 - the estimate table is terminal output


def confirm() -> str:
    return input("Proceed? [y/N]: ")  # noqa: T201 - interactive confirmation
