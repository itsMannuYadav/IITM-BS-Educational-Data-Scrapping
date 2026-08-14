import json

# Check what's in the HTML pages for actual content
pages = [
    'data/raw/oppepractice/page_app_subjects.html',
    'data/raw/oppepractice/page_leaderboard.html'
]

for page_path in pages:
    try:
        with open(page_path) as f:
            content = f.read()
            
        print(f"\n{'='*60}")
        print(f"FILE: {page_path}")
        print(f"{'='*60}")
        
        # Look for drive links
        import re
        drive_urls = re.findall(r'https?://(?:drive|docs)\.google\.com/[^\s"\'<>]+', content)
        if drive_urls:
            print(f"\n✓ Google Drive URLs found: {len(drive_urls)}")
            for url in drive_urls[:5]:
                print(f"  {url[:80]}")
        else:
            print("\n✗ No Google Drive URLs")
        
        # Look for direct file downloads
        file_urls = re.findall(r'https?://[^\s"\'<>]*\.(pdf|xlsx|docx?|zip|txt)', content)
        if file_urls:
            print(f"\n✓ File URLs found: {len(file_urls)}")
            for url in file_urls[:5]:
                print(f"  {url[0]}")
        else:
            print("\n✗ No direct file URLs")
        
        # Look for API endpoints or data
        api_calls = re.findall(r'(?:fetch|axios|api).*?["\'](/api/[^"\']+)["\']', content)
        if api_calls:
            print(f"\n✓ API endpoints referenced: {len(set(api_calls))}")
            for api in sorted(set(api_calls))[:10]:
                print(f"  {api}")
        
        # Look for questions or problems
        if 'question' in content.lower():
            print("\n✓ Contains 'question' data")
        if 'problem' in content.lower():
            print("\n✓ Contains 'problem' data")
        if 'leaderboard' in content.lower():
            print("\n✓ Contains 'leaderboard' data")
            
    except Exception as e:
        print(f"Error reading {page_path}: {e}")

# Check API responses
print(f"\n\n{'='*60}")
print("API RESPONSES")
print(f"{'='*60}")
try:
    with open('data/raw/oppepractice/api_responses_auth.json') as f:
        apis = json.load(f)
    
    for endpoint, resp in apis.items():
        print(f"\n{endpoint}:")
        if isinstance(resp, dict):
            print(f"  Status: {resp.get('status', 'unknown')}")
            if 'text' in resp:
                print(f"  Text preview: {str(resp.get('text', ''))[:100]}")
            if 'data' in resp:
                print(f"  Has data: {type(resp.get('data'))}")
            print(f"  Keys: {str(list(resp.keys())[:8])[:80]}")
except Exception as e:
    print(f"Error: {e}")
