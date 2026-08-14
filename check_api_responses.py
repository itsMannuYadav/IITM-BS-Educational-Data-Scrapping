import json

with open('data/raw/oppepractice/api_responses_auth.json') as f:
    apis = json.load(f)

for endpoint in ['/api/subjects/python/questions', '/api/subjects/dbms/questions']:
    if endpoint in apis:
        resp = apis[endpoint]
        print(f'\n{endpoint}:')
        print(f'  Status: {resp.get("status")}')
        data = resp.get('data')
        if isinstance(data, dict):
            print(f'  Type: dict')
            print(f'  Keys: {list(data.keys())[:15]}')
            print(f'  Size: {len(json.dumps(data))} bytes')
        elif isinstance(data, list):
            print(f'  Type: list with {len(data)} items')
            if data:
                print(f'  First item keys: {list(data[0].keys())[:10] if isinstance(data[0], dict) else "N/A"}')
        else:
            print(f'  Type: {type(data).__name__}')
