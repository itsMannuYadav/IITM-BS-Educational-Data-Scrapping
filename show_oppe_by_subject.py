import json

with open('data/index.jsonl') as f:
    all_records = [json.loads(line) for line in f]
    
oppe = [r for r in all_records if r.get('source') == 'oppepractice']
print(f'Total OPPE records: {len(oppe)}\n')

# Group by program/subject
by_subject = {}
for r in oppe:
    subject = r.get('program') or 'General'
    by_subject[subject] = by_subject.get(subject, 0) + 1

print('By Subject/Program:')
for subj, count in sorted(by_subject.items(), reverse=True):
    print(f'  {subj:20} : {count:3} resources')
print()

# Show some sample Python questions
python_q = [r for r in oppe if r.get('program') == 'PYTHON']
print(f'Python Questions (showing first 10 of {len(python_q)}):')
for r in python_q[:10]:
    title = r.get('title', '?')[:55]
    topic = r.get('extra', {}).get('topic', '')[:20]
    diff = r.get('extra', {}).get('difficulty', '')
    print(f'  {title:55} | {topic:20} | {diff}')
print()

# Show some sample DBMS questions
dbms_q = [r for r in oppe if r.get('program') == 'DBMS']
print(f'DBMS Questions (showing first 10 of {len(dbms_q)}):')
for r in dbms_q[:10]:
    title = r.get('title', '?')[:55]
    topic = r.get('extra', {}).get('topic', '')[:20]
    diff = r.get('extra', {}).get('difficulty', '')
    print(f'  {title:55} | {topic:20} | {diff}')
