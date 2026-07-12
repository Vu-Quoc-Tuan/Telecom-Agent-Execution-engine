import argparse
import codecs
import json
import logging
import re

logging.basicConfig(level=logging.WARNING, format="%(message)s")

DEFAULT_PATTERNS = {
    "ips": r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b",
    "ne_names": r"\b[A-Z]{3,4}-[A-Z]{3,4}-[0-9]{2,4}\b",
    "interfaces": r"\b(?:GigabitEthernet|FastEthernet|Ethernet|TenGigE|Ge|Eth|Fa)[0-9]+/[0-9]+(?:/[0-9]+)?(?:\.[0-9]+)?\b",
    "cell_ids": r"\b(?:[A-Z]{3,4}_[0-9]{4}|cell[-_][0-9a-zA-Z]+)\b",
    "as_numbers": r"\bAS[0-9]+\b",
    "vlans": r"\bvlan[-_]?[0-9]+\b",
}


def extract_entities(text, custom_patterns=None):
    patterns = dict(DEFAULT_PATTERNS)
    if custom_patterns:
        patterns.update(custom_patterns)

    extracted = {k: [] for k in patterns.keys()}
    extracted["kv"] = {}

    # 1. Extract standard entities
    for key, regex_str in patterns.items():
        if isinstance(regex_str, dict):
            r = re.compile(regex_str.get("regex"))
            grp = regex_str.get("group", 0)
            matches = []
            for m in r.finditer(text):
                matches.append(m.group(grp))
            extracted[key] = sorted(list(set(matches)))
        else:
            matches = re.findall(regex_str, text, re.IGNORECASE)
            extracted[key] = sorted(list(set(matches)))

    # 2. Extract key=value pairs
    kv_pattern = re.compile(r"\b([a-zA-Z0-9_-]+)=([a-zA-Z0-9_-]+)\b")
    for k, v in kv_pattern.findall(text):
        extracted["kv"][k] = v

    # Generate lookup keys (e.g. IPs and NE names)
    lookup_keys = sorted(list(set(extracted.get("ips", []) + extracted.get("ne_names", []))))

    return extracted, lookup_keys


def main():
    parser = argparse.ArgumentParser(description="NOC Alarm Content Entity Extractor")
    parser.add_argument("--text", help="Single text content to parse")
    parser.add_argument("--input", help="Path to input JSONL file containing batch alarms")
    parser.add_argument(
        "--content-field", default="content", help="Field name containing alarm text"
    )
    parser.add_argument(
        "--patterns", help="JSON string or path to JSON file containing custom override patterns"
    )

    args = parser.parse_args()

    # Overlay arguments from args.json if running in the sandbox
    try:
        with open("args.json", encoding="utf-8") as f:
            sandbox_args = json.load(f)
            if "text" in sandbox_args:
                args.text = sandbox_args["text"]
            if "input" in sandbox_args:
                args.input = sandbox_args["input"]
            if "content_field" in sandbox_args:
                args.content_field = sandbox_args["content_field"]
            elif "content-field" in sandbox_args:
                args.content_field = sandbox_args["content-field"]
            if "patterns" in sandbox_args:
                args.patterns = sandbox_args["patterns"]
    except FileNotFoundError:
        pass

    custom_patterns = None
    if args.patterns:
        if isinstance(args.patterns, dict):
            custom_patterns = args.patterns
        elif isinstance(args.patterns, str):
            try:
                custom_patterns = json.loads(args.patterns)
            except json.JSONDecodeError:
                try:
                    with codecs.open(args.patterns, "r", encoding="utf-8") as f:
                        custom_patterns = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    parser.error(f"Failed to load custom patterns: {exc}")
        else:
            parser.error("patterns must be a JSON object, JSON string, or JSON file path")

        if not isinstance(custom_patterns, dict):
            parser.error("patterns must resolve to a JSON object")

    if not args.text and not args.input:
        parser.error("one of --text or --input is required")

    if args.text:
        extracted, lookup_keys = extract_entities(args.text, custom_patterns)
        result = {"text": args.text, "extracted": extracted, "lookup_keys": lookup_keys}
        print(json.dumps(result, indent=2))

    elif args.input:
        if args.input == "-":
            parser.error("--input - is not supported; provide a workspace-relative JSONL path")
        with codecs.open(args.input, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

        all_lookup_keys = set()
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            content = record.get(args.content_field, "")
            extracted, lookup_keys = extract_entities(content, custom_patterns)
            record["extracted"] = extracted
            record["lookup_keys"] = lookup_keys
            all_lookup_keys.update(lookup_keys)
            print(json.dumps(record))

        logging.warning("LOOKUP_KEYS=%s", json.dumps(sorted(all_lookup_keys)))
    else:
        parser.error("one of --text or --input is required")


if __name__ == "__main__":
    main()
