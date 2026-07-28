import requests, json

H = {'xc-token': '[REDACTED]', 'Content-Type': 'application/json'}
BASE = 'http://[SERVER_IP]'

# List tables in new base
r = requests.get(f'{BASE}/api/v2/meta/bases/pjg5uoh4uqz2xhp/tables', headers=H)
print(f'Status: {r.status_code}')
if r.ok:
    data = r.json()
    for t in data.get('list', []):
        tid = t.get('id')
        title = t.get('title')
        print(f'  {tid}: {title}')
else:
    print(r.text[:200])
