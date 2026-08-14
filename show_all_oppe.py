import json

with open('data/index.jsonl') as f:
    all_records = [json.loads(line) for line in f]
    
oppe = [r for r in all_records if r.get('source') == 'oppepractice']
print(f'Total OPPE records: {len(oppe)}\n')

# Group by type
by_type = {}
for r in oppe:
    rtype = r.get('type', '?')
    by_type[rtype] = by_type.get(rtype, 0) + 1

print('By Resource Type:')
for rtype, count in sorted(by_type.items()):
    print(f'  {rtype}: {count}')
print()

# Show all titles
print('All Indexed Resources:')
for i, r in enumerate(oppe, 1):
    title = r.get('title', '?')[:65]
    program = r.get('program', 'General')[:10]
    rtype = r.get('type', '?')[:15]
    print(f'{i:2}. [{rtype:13}] {program:12} | {title}')
