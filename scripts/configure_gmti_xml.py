#!/usr/bin/env python3
"""Create a GMTI XML variant with explicit key=value overrides."""

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument(
        "--set-json", action="append", default=[], type=Path,
        metavar="PATH",
        help="load XML overrides from a JSON object or an object under xml_overrides")
    args = parser.parse_args()

    tree = ET.parse(args.input)
    root = tree.getroot()
    params = root.find("GMTI_parameter")
    if params is None:
        raise SystemExit("missing <GMTI_parameter>")
    overrides = []
    for path in args.set_json:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"cannot read JSON override file {path}: {exc}") from exc
        if isinstance(payload, dict) and "xml_overrides" in payload:
            payload = payload["xml_overrides"]
        if not isinstance(payload, dict):
            raise SystemExit(f"JSON override file must contain an object: {path}")
        overrides.extend((str(key), value) for key, value in payload.items())
    overrides.extend((item.split("=", 1)[0].strip(), item.split("=", 1)[1])
                     for item in args.set if "=" in item)
    invalid = [item for item in args.set if "=" not in item]
    if invalid:
        raise SystemExit(f"invalid --set value: {invalid[0]!r}")

    for key, value in overrides:
        key = key.strip()
        if not key or "." in key:
            raise SystemExit(f"invalid XML key in override: {key!r}")
        node = params.find(key)
        if node is None:
            node = ET.SubElement(params, key)
        if isinstance(value, bool):
            node.text = "1" if value else "0"
        elif value is None:
            raise SystemExit(f"null XML value for key: {key!r}")
        else:
            node.text = str(value)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(args.output, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
