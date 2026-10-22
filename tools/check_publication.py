"""Local publication checks. Findings never print matching secret values."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential in URL": re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
    "personal Windows path": re.compile(
        r"(?i)[a-z]:(?:\\+|/)Users(?:\\+|/)(?!Public\b|Default\b|name\b|user\b)[^\\/\s\"']+"
    ),
}


def findings(content: bytes, name: str) -> list[str]:
    if b"\0" in content:
        return []
    text = content.decode("utf-8", errors="replace")
    found = [label for label, pattern in PATTERNS.items() if pattern.search(text)]
    if name.endswith(".ipynb"):
        notebook = json.loads(text)
        if any(cell.get("outputs") or cell.get("execution_count") is not None
               for cell in notebook.get("cells", [])):
            found.append("saved notebook output/execution")
        if notebook.get("metadata", {}).get("widgets"):
            found.append("saved notebook widget state")
    if name == "config.json" and json.loads(text).get("recorder", {}).get("base_dir"):
        found.append("non-default recordings folder")
    return found


def check(root: Path, history: bool = False) -> list[dict[str, str]]:
    results = []
    if history:
        objects = subprocess.check_output(
            ["git", "rev-list", "--objects", "--all"], cwd=root, text=True
        ).splitlines()
        process = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        try:
            for entry in objects:
                object_id, _, name = entry.partition(" ")
                if not name:
                    continue
                process.stdin.write(f"{object_id}\n".encode())
                process.stdin.flush()
                header = process.stdout.readline().split()
                content = process.stdout.read(int(header[2]))
                process.stdout.read(1)
                if header[1] == b"blob":
                    for label in findings(content, name):
                        results.append({"path": name, "object": object_id, "finding": label})
        finally:
            process.stdin.close()
            process.stdout.close()
            process.wait()
    else:
        names = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
        ).decode().split("\0")
        for name in sorted(set(filter(None, names))):
            path = root / name
            if path.is_file():
                for label in findings(path.read_bytes(), name):
                    results.append({"path": name, "finding": label})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Inspect all reachable Git history")
    parser.add_argument("--report", type=Path, help="Write redacted JSON findings")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    results = check(root, args.history)
    if args.report:
        args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for result in results[:30]:
        print(f"{result['path']}: {result['finding']}"
              + (f" (blob {result['object'][:12]})" if "object" in result else ""))
    print(f"{len(results)} findings. This pattern-based check is not a complete secret/data audit.")
    return int(bool(results))


if __name__ == "__main__":
    raise SystemExit(main())
