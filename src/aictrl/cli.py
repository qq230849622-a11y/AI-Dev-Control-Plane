import argparse
import sys

from . import __version__
from .registry import route_path
from .validator import validate_path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aictrl")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("path")
    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("--registry", required=True)
    route_parser.add_argument("envelope")
    arguments = parser.parse_args(argv)

    if arguments.command == "validate":
        result = validate_path(arguments.path)
        stream = sys.stdout if result.valid else sys.stderr
        print(result.message, file=stream)
        return 0 if result.valid else 1

    if arguments.command == "route":
        result = route_path(arguments.registry, arguments.envelope)
        stream = sys.stdout if result.valid else sys.stderr
        print(result.message, file=stream)
        return 0 if result.valid else 1

    return 0
