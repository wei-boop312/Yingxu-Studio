"""Require a row where SQL callers previously indexed an optional fetch result."""

from typing import Protocol, TypeVar

Row_co = TypeVar("Row_co", covariant=True)
Row = TypeVar("Row")


class RowCursor(Protocol[Row_co]):
    def fetchone(self) -> Row_co | None: ...


def required_row(cursor: RowCursor[Row]) -> Row:
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("required_database_row_missing")
    return row
