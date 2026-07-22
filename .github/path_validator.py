import yaml
import json
import sys
import re

author = sys.argv[1]
changed_files = json.loads(sys.argv[2])  # 现在changed_files是字符串列表

with open('.github/allowed_paths.yml') as f:
    rules = yaml.safe_load(f)

if author not in rules:
    print(f"❌ {author} not in allowed_paths.yml")
    exit(1)

patterns = [p.replace('**', '.*') for p in rules[author]]
for file in changed_files:
    if not any(re.match(pattern, file) for pattern in patterns):
        print(f"❌ {file} not allowed for {author}")
        exit(1)
print("✅ All changes in allowed paths")