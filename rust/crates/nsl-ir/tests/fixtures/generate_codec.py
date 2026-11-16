"""Write the canonical scalar/escaping fixture using the frozen Oracle."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sys


def main(oracle_root: Path, corpus_root: Path) -> None:
    sys.path.insert(0, str(oracle_root.resolve()))
    from nsl.core import BOOL, DECIMAL, INT, STRING, Money, money_type
    from nsl.ir import LetStatement, LiteralExpr, NsoCodec

    base = NsoCodec.decode((corpus_root / "ir-1-0-canonical" / "expected.nso").read_bytes())
    cases = (
        (False, BOOL),
        (2**128 + 1, INT),
        ("예산", STRING),
        (Decimal("-0.00"), DECIMAL),
        (Money(Decimal("0.10"), "KRW"), money_type("KRW")),
        ('"\\/\b\f\n\r\t\x00\x1f\x7f한글🌏\u2028', STRING),
    )
    body = tuple(
        LetStatement(f"let-{index}", f"s{index}", LiteralExpr(f"literal-{index}", value, kind))
        for index, (value, kind) in enumerate(cases)
    )
    skill = replace(base, body=body).with_computed_hash()
    encoded = NsoCodec.encode(skill)
    assert NsoCodec.decode(encoded) == skill
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
