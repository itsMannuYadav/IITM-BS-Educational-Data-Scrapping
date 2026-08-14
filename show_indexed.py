import json

with open('data/index.jsonl') as f:
    records = [json.loads(line) for line in f if 'oppepractice' in line]

print(f'✓ Total oppepractice resources indexed: {len(records)}\n')
for i, r in enumerate(records, 1):
    title = r.get("title", "?")
    rtype = r.get("type", "?")
    program = r.get("program", "?") or "(none)"
    authenticated = "✓ AUTHENTICATED" if r.get('extra', {}).get('authenticated') else ""
    
    print(f'{i}. {title}')
    print(f'   Type: {rtype} | Program: {program} {authenticated}')
    print()
