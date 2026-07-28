import requests
H = {'xc-token': '[REDACTED]', 'Content-Type': 'application/json'}
BASE = 'http://[SERVER_IP]'
T = 'mt5248fkcpapp5a'

# Try writing to SingleSelect columns with new values
# NocoDB might auto-create options
test_row = {
    "素材名称": "TEST_AUTO_OPTIONS",
    "游戏名称": "心动防线",
    "hook_策略": "反常识标题",
    "场景元素": "弹球/战斗",
    "制作方式": "K帧动画",
    "叙事模式": "问题-解法",
    "画面风格": "二次元精致渲染",
    "标签状态": "AI建议",
    "开头策略": "反常识标题",
    "视觉风格": "二次元精致渲染",
    "文案角度": "问题-解法",
    "BGM类型": "激昂",
}

url = f'{BASE}/api/v2/tables/{T}/records'
r = requests.post(url, headers=H, json=[test_row], timeout=30)
print(f'Status: {r.status_code}')
print(f'Response: {r.text[:300]}')

if r.ok:
    # Verify
    r2 = requests.get(url, headers=H, params={'limit': 1, 'sort': '-Id'})
    row = r2.json().get('list', [{}])[0]
    print(f'\nWritten:')
    for k in ['素材名称', 'hook_策略', '场景元素', '标签状态']:
        print(f'  {k}: {row.get(k)}')
