import fnmatch
import json
import sys

import yaml


if len(sys.argv) != 3:
    print("❌ Usage: path_validator.py <pr_author> <changed_files_json>")
    sys.exit(1)

author = sys.argv[1]

try:
    changed_files = json.loads(sys.argv[2])
except json.JSONDecodeError as exc:
    print(f"❌ Invalid changed-files JSON: {exc}")
    sys.exit(1)

if not isinstance(changed_files, list):
    print("❌ Changed files must be a JSON list")
    sys.exit(1)

with open(".github/allowed_paths.yml", encoding="utf-8") as file:
    rules = yaml.safe_load(file) or {}

if author not in rules:
    print(f"❌ {author} not in allowed_paths.yml")
    sys.exit(1)

patterns = rules[author]

for changed_file in changed_files:
    if not any(
        fnmatch.fnmatchcase(changed_file, pattern)
        for pattern in patterns
    ):
        print(f"❌ {changed_file} not allowed for {author}")
        sys.exit(1)

print(f"✅ All {len(changed_files)} changed files are allowed for {author}")