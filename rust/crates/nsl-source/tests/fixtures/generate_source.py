"""Generate SourceFile, lexer-position, and language header observations."""

from pathlib import Path
import sys


def token(text: str) -> str:
    return text.encode("utf-8").hex() or "-"


def position(value) -> str:
    return f"{value.offset},{value.line},{value.column}"


def main(oracle_root: Path) -> None:
    sys.path.insert(0, str(oracle_root.resolve()))
    from nsl.diagnostics import CompileError
    from nsl.source import SourceFile, SourceId, coerce_source
    from nsl.syntax import LanguageHeaderScanner, Lexer

    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    sources = (
        ("empty", "empty.ns", b"", None),
        ("utf8", "재무/예산.ns", "예산😀 e\u0301\r\n".encode(), None),
        ("bom", "bom.ns", b"\xef\xbb\xbf" + "예산".encode(), None),
        ("bare", "bom.ns", "예산".encode(), None),
        ("bom_only", "empty.ns", b"\xef\xbb\xbf", None),
        ("double_bom", "bom.ns", b"\xef\xbb\xbf\xef\xbb\xbf" + b"x", None),
        ("interior_bom", "bom.ns", b"x\xef\xbb\xbf", "source:explicit"),
        ("blank_override", "custom.ns", b"x", ""),
        ("space_override", "custom.ns", b"x", " "),
        ("nul_in_text", "nul.ns", b"a\x00b", None),
        ("nul_in_path", "a\x00b.ns", b"x", None),
        ("path_changes_identity", "another.ns", "예산".encode(), None),
        ("text_changes_identity", "bom.ns", "예산\n".encode(), None),
        ("windows_path_preserved", "a\\b.ns", b"x", None),
        ("relative_path_preserved", "../x.ns", b"x", None),
        ("extension_only", ".ns", b"x", None),
        ("wrong_extension", "x.NS", b"x", None),
        ("no_extension", "", b"x", None),
        ("stray_byte", "x.ns", b"\x80", None),
        ("overlong", "x.ns", b"\xc0\xaf", None),
        ("surrogate", "x.ns", b"\xed\xa0\x80", None),
        ("out_of_range", "x.ns", b"\xf4\x90\x80\x80", None),
        ("truncated", "x.ns", b"\xf0\x9f\x8c", None),
        ("invalid_after_bom", "x.ns", b"\xef\xbb\xbf\xff", None),
        ("utf8_validated_before_path", "bad", b"\xff", None),
    )
    for label, path, payload, requested in sources:
        args = (label, token(path), payload.hex() or "-", "none" if requested is None else token(requested))
        try:
            source = SourceFile.from_bytes(path, payload, None if requested is None else SourceId(requested))
        except ValueError as error:
            print("source_error", *args, str(error), sep="\t")
        else:
            print("source", *args, token(source.text), source.source_id, source.encoding, sep="\t")

    # The Oracle lexer supplies authoritative scalar offsets and positions.
    for text in ('', 'language\r\n  "예산😀e\u0301" // 주석\nskill', '"a\r\n한"\r\t"끝"\n',
                 '// 설명 😀\n"e\u0301"\n"줄\u2028나눔"', '"a"\r"b"'):
        source = coerce_source(text)
        for item in Lexer().tokenize(source):
            span = item.span
            start = len(text[:span.start.offset].encode("utf-8"))
            end = len(text[:span.end.offset].encode("utf-8"))
            print("span", token(text), start, end, position(span.start), position(span.end),
                  span.source_id, source.logical_path, sep="\t")

    declaration = 'language NSL "0.1";'
    byte_limit = LanguageHeaderScanner.MAX_HEADER_BYTES
    headers = [
        ("legacy", "text", "", "", 0, declaration),
        ("controlled", "text", "", "", 0, 'language NSL "0.2"; skill'),
        ("utf8_comments", "text", "// 설명😀\r\n\t", "", 0, 'language // a\n NSL // b\n "0.2" // c\n ;\nskill'),
        ("bom_bytes", "bytes", "\ufeff", "", 0, declaration),
        ("bom_text", "text", "\ufeff", "", 0, declaration),
        ("only_comment", "text", "// 끝", "", 0, ""),
        ("empty", "text", "", "", 0, ""),
        ("missing_other_word", "text", "skill X", "", 0, ""),
        ("non_leading", "text", ' \t"예산" language NSL "0.1";', "", 0, ""),
        ("keyword_embedded", "text", "skill Xylanguage language_ language2", "", 0, ""),
        ("non_ascii_word_boundary", "text", "한language", "", 0, ""),
        ("missing_name", "text", "language", "", 0, ""),
        ("wrong_name", "text", 'language NSL2 "0.1";', "", 0, ""),
        ("missing_quote", "text", "language NSL 0.2;", "", 0, ""),
        ("missing_end_quote", "text", 'language NSL "0.2', "", 0, ""),
        ("missing_semicolon", "text", 'language NSL "0.2" ', "", 0, ""),
        ("escaped_version", "text", 'language NSL "0.\\2";', "", 0, ""),
        ("version_newline", "text", 'language NSL "0.\r\n2";', "", 0, ""),
        ("unsupported_version", "text", 'language NSL "0.9";', "", 0, ""),
        ("empty_version", "text", 'language NSL "";', "", 0, ""),
        ("non_ascii_version", "text", 'language NSL "０.２";', "", 0, ""),
        ("duplicate", "text", declaration + '// 설명\r\n language NSL "0.2";', "", 0, ""),
        ("duplicate_keyword_only", "text", declaration + ' language', "", 0, ""),
        ("before_ascii_byte_limit", "text", "", " ", byte_limit - len(declaration) - 1, declaration),
        ("at_ascii_byte_limit", "text", "", " ", byte_limit - len(declaration), declaration),
        ("after_ascii_byte_limit", "text", "", " ", byte_limit - len(declaration) + 1, declaration),
        ("comment_exceeds_utf8_byte_limit", "text", "//", "é", 2048, "\n" + declaration),
        ("probe_at_limit", "text", "?", " ", byte_limit - 2, "language"),
        ("probe_after_limit", "text", "?", " ", byte_limit - 1, "language"),
        ("duplicate_at_limit", "text", declaration, " ", byte_limit - len(declaration), "language"),
        ("duplicate_after_limit", "text", declaration, " ", byte_limit - len(declaration) + 1, "language"),
        ("duplicate_after_crossing_limit", "text", declaration + "//", "é", 2039, "language"),
        ("bounded_comment_tail", "text", declaration + "//", "한", byte_limit, "\n" + declaration),
    ]
    for label, mode, prefix, repeat, count, tail in headers:
        text = prefix + repeat * count + tail
        source = (SourceFile.from_bytes("header.ns", text.encode()) if mode == "bytes"
                  else SourceFile.from_text("header.ns", text))
        args = (label, mode, token(prefix), token(repeat), count, token(tail))
        try:
            header = LanguageHeaderScanner().scan(source)
        except CompileError as error:
            location = error.location
            assert location is not None
            lines = source.text.split("\n")
            offset = sum(len(line) + 1 for line in lines[:location.line - 1]) + location.column - 1
            print("header_error", *args, error.code, f"{offset},{location.line},{location.column}", sep="\t")
        else:
            print("header", *args, header.version, position(header.span.start), position(header.span.end),
                  position(header.version_span.start), position(header.version_span.end), sep="\t")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
