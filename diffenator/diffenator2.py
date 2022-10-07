#!/usr/bin/env python3
import argparse
from fontTools.ttLib import TTFont
from diffenator import run_diffing_tools, run_proofing_tools


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar='"proof" or "diff"'
    )
    universal_options_parser = argparse.ArgumentParser(add_help=False)
    universal_options_parser.add_argument(
        "--out", "-o", help="Output dir", default="out"
    )
    universal_options_parser.add_argument(
        "--imgs", help="Generate images", action="store_true", default=False
    )
    proof_parser = subparsers.add_parser(
        "proof",
        parents=[universal_options_parser],
        help="Generate html proofing documents for a family",
    )
    proof_parser.add_argument("fonts", nargs="+")

    diff_parser = subparsers.add_parser(
        "diff",
        parents=[universal_options_parser],
    )
    diff_parser.add_argument("--fonts-before", "-fb", nargs="+", required=True)
    diff_parser.add_argument("--fonts-after", "-fa", nargs="+", required=True)

    args = parser.parse_args()

    if args.command == "proof":
        fonts = [TTFont(f) for f in args.fonts]
        run_proofing_tools(fonts, out=args.out, imgs=args.imgs)
    elif args.command == "diff":
        fonts_before = [TTFont(f) for f in args.fonts_before]
        fonts_after = [TTFont(f) for f in args.fonts_after]
        run_diffing_tools(fonts_before, fonts_after, out=args.out, imgs=args.imgs)
    else:
        raise NotImplementedError(f"{args.command} not supported")


if __name__ == "__main__":
    main()