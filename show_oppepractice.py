import json

with open('data/index.jsonl') as f:
    records = [json.loads(line) for line in f if 'oppepractice' in line]

print(f'Found {len(records)} oppepractice records:\n')
for r in records:
    print(f'  - {r.get("title", "?")}')
    print(f'    Type: {r.get("type", "?")}')
    print(f'    URL: {r.get("original_url", "?")}')
    print()
