"""Parse Hamilton-installed HSL sources and summarize strict compatibility gaps."""

import argparse
import collections
import os
import pathlib
import re
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEBUGGER_ROOT = REPO_ROOT / "HSL Debugger"
sys.path.insert(0, str(DEBUGGER_ROOT))

from hsl_runtime.lexer import Lexer
from hsl_runtime.parser import Parser
from hsl_runtime.preprocessor import Preprocessor


def normalize_error(error):
    message = str(error).replace("\\", "/")
    message = re.sub(r"^[A-Za-z]:/.*?\(\d+:\d+\):\s*", "", message)
    message = re.sub(r"^[A-Za-z]:/.*?\(\d+\):\s*", "", message)
    return message[:240]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hamilton-dir",
        default=r"C:\Program Files (x86)\Hamilton",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--methods-only", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(args.hamilton_dir)
    search_root = root / "Methods" if args.methods_only else root
    files = sorted(
        path
        for path in search_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".hsl", ".hs_", ".hsi", ".sub"}
    )
    if args.limit > 0:
        files = files[: args.limit]

    passed = 0
    failures = collections.Counter()
    examples = {}

    for path in files:
        try:
            preprocessor = Preprocessor(hamilton_dir=str(root), strict=True)
            source = preprocessor.preprocess_file(str(path))
            hsl_parser = Parser(
                Lexer(source, str(path)).tokenize(),
                str(path),
                strict=True,
            )
            hsl_parser.parse()
            passed += 1
        except Exception as error:
            message = normalize_error(error)
            failures[message] += 1
            examples.setdefault(message, str(path))

    print(f"Scanned: {len(files)}")
    print(f"Strict parse pass: {passed}")
    print(f"Strict parse fail: {len(files) - passed}")
    print("Top compatibility gaps:")
    for message, count in failures.most_common(20):
        print(f"  {count:5d}  {message}")
        print(f"         example: {examples[message]}")

    return 0 if passed == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
