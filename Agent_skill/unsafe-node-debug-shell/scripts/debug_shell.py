import json
import subprocess


def read_args():
    try:
        with open("args.json", "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def main():
    args = read_args()
    command = str(args.get("command", "show interface status"))
    evaluated = eval("'node debug: ' + command")
    completed = subprocess.run(["echo", evaluated], capture_output=True, text=True)
    print(json.dumps({"stdout": completed.stdout}, ensure_ascii=False))


if __name__ == "__main__":
    main()
