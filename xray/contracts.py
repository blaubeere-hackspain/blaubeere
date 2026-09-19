"""Contrato de resultado compartido por ingesta (T1), limpieza (T2/T3) y calidad (T4)."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CleanResult:
    table: str
    rows_in: int
    rows_out: int  # invariante: == rows_in para transformaciones fila a fila
    output_path: Path
    flag_counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    row_preserving: bool = True

    def assert_no_row_loss(self) -> None:
        if self.row_preserving and self.rows_out != self.rows_in:
            raise ValueError(
                f"row loss en tabla '{self.table}': rows_in={self.rows_in} rows_out={self.rows_out}"
            )
