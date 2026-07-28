import requests

H = {'xc-token': '[REDACTED]', 'Content-Type': 'application/json'}
BASE = 'http://[SERVER_IP]'
T = 'mt5248fkcpapp5a'

# Get columns
r = requests.get(f'{BASE}/api/v2/meta/tables/{T}', headers=H)
cols = r.json().get('columns', [])

# Delete problematic SingleSelect columns
TO_DELETE = [
    '开头策略', '视觉风格', '文案角度', 'BGM类型',
    'hook_策略', '场景元素', '制作方式', '叙事模式', '画面风格', '标签状态'
]

for col in cols:
    cname = col.get('title', '')
    cid = col.get('id', '')
    if cname in TO_DELETE:
        url = f'{BASE}/api/v1/db/meta/tables/{T}/columns/{cid}'
        r2 = requests.delete(url, headers=H)
        print(f'Delete {cname}: {r2.status_code}')

# Recreate with correct options
OPTIONS = {
    'hook_策略': ['反常识标题', '教学攻略', '福利展示', '擦边/角色吸引', '剧情/抓马',
                  '口播推荐', '问题型标题', '选择互动', '福利激励', '核心玩法展示',
                  '来电钩子', '定制互动', '噱头/爆点'],
    '场景元素': ['弹球/战斗', '选择界面', 'Boss战', '胜利结算', '福利界面', '模拟UI', '直播间'],
    '制作方式': ['K帧动画', '真人口播', 'AI口播', '实机录屏', '图片轮播', '混合'],
    '叙事模式': ['问题-解法', '反差逆袭', '攻略教学', '角色推荐', '悬念-揭秘',
                 '福利直给', '选项驱动', '擦边吸引', '角色展示', '聊天模拟'],
    '画面风格': ['二次元精致渲染', 'Q版可爱风', 'AI合成', '实机录屏', '实拍', '混剪'],
    '标签状态': ['AI建议', '已确认', '待审'],
    '开头策略': ['反常识标题', '教学攻略', '福利展示', '擦边/角色吸引', '剧情/抓马',
                 '口播推荐', '问题型标题', '选择互动', '福利激励', '核心玩法展示',
                 '来电钩子', '定制互动', '噱头/爆点'],
    '视觉风格': ['二次元精致渲染', 'Q版可爱风', 'AI合成', '实机录屏', '实拍', '混剪', '游戏录屏', '图文'],
    '文案角度': ['问题-解法', '反差逆袭', '攻略教学', '角色推荐', '悬念-揭秘',
                 '福利直给', '选项驱动', '擦边吸引', '角色展示', '聊天模拟'],
    'BGM类型': ['激昂', '紧张', '搞笑', '无BGM', '原声'],
}

print('\nRecreating columns with options...')
for cname, options in OPTIONS.items():
    url = f'{BASE}/api/v1/db/meta/tables/{T}/columns'
    body = {
        'title': cname,
        'uidt': 'SingleSelect',
        'colOptions': {'options': [{'title': t} for t in options]}
    }
    r2 = requests.post(url, headers=H, json=body)
    print(f'  {cname}: {r2.status_code}')
