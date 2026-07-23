import fnmatch
import os
import sys

import yaml


if len(sys.argv) != 2:
    print("❌ Usage: path_validator.py <pr_author>")
    sys.exit(1)

author = sys.argv[1]

# 从标准输入读取由 NUL 分隔的文件名，避免空格等特殊字符造成解析错误
changed_files = [
    os.fsdecode(item)
    for item in sys.stdin.buffer.read().split(b"\0")
    if item
]

with open(".github/allowed_paths.yml", encoding="utf-8") as file:
    rules = yaml.safe_load(file) or {}

if author not in rules:
    print(f"❌ {author} not in allowed_paths.yml")
    sys.exit(1)

allowed_patterns = rules[author]

for changed_file in changed_files:
    if not any(
        fnmatch.fnmatchcase(changed_file, pattern)
        for pattern in allowed_patterns
    ):
        print(f"❌ {changed_file} not allowed for {author}")
        sys.exit(1)

print(f"✅ All {len(changed_files)} changed files are allowed for {author}")