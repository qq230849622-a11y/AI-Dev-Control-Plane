import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aictrl.pre003 import admit_issue_comment, has_matching_pong


def _comments(path):
    # Windows PowerShell 5.1 writes UTF-8 with a BOM for Set-Content -Encoding utf8.
    document = json.loads(Path(path).read_text(encoding="utf-8-sig"))

    def flatten(value):
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [comment for item in value for comment in flatten(item)]
        return []

    return flatten(document)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--comments", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)

    event = json.loads(Path(arguments.event).read_text(encoding="utf-8"))
    result = admit_issue_comment(event)
    duplicate = result.admitted and has_matching_pong(
        _comments(arguments.comments), result.event_id
    )
    output = [
        f"admitted={'true' if result.admitted else 'false'}",
        f"event_id={result.event_id or ''}",
        f"duplicate={'true' if duplicate else 'false'}",
        f"reason={result.reason or ''}",
    ]
    Path(arguments.output).write_text("\n".join(output) + "\n", encoding="utf-8")
    print("ADMITTED" if result.admitted else "REJECTED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
