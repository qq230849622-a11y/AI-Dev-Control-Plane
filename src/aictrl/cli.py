import argparse

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aictrl")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.parse_args(argv)
    return 0
