import json

with open('data/raw/oppepractice/api_responses_auth.json') as f:
    apis = json.load(f)

for endpoint in ['/api/subjects/python/questions']:
    if endpoint in apis:
        resp = apis[endpoint]
        data = resp.get('data', {})
        rows = data.get('rows', [])
        
        if rows:
            print(f'Sample Python question structure:\n')
            q = rows[0]
            print(json.dumps(q, indent=2)[:1000])
            print(f'\n\nAvailable fields in questions:')
            print(json.dumps(list(q.keys()), indent=2))
