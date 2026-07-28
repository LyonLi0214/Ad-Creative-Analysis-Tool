import requests, json, time

H = {'xc-token': '[REDACTED]', 'Content-Type': 'application/json'}
BASE = 'http://[SERVER_IP]'
BASE_ID = 'pjg5uoh4uqz2xhp'

def create_table(title, columns):
    url = f'{BASE}/api/v2/meta/bases/{BASE_ID}/tables'
    body = {'table_name': title, 'title': title, 'columns': columns}
    r = requests.post(url, headers=H, json=body, timeout=30)
    if r.ok:
        tid = r.json().get('id')
        print(f'  + {title}: {tid}')
        return tid
    else:
        print(f'  ! {title}: {r.text[:150]}')
        return None

# Table 1: 素材主表
T1 = create_table('素材主表', [
    {'title': '素材名称', 'uidt': 'SingleLineText'},
    {'title': '游戏名称', 'uidt': 'SingleLineText'},
    {'title': '平台', 'uidt': 'SingleLineText'},
    {'title': '类型', 'uidt': 'SingleLineText'},
    {'title': 'Campaign', 'uidt': 'SingleLineText'},
    {'title': '链接', 'uidt': 'URL'},
    {'title': '入库时间', 'uidt': 'DateTime'},
    {'title': '状态', 'uidt': 'SingleLineText'},
    {'title': '上传人', 'uidt': 'SingleLineText'},
    {'title': '花费', 'uidt': 'Decimal'},
    {'title': '点击率', 'uidt': 'Decimal'},
    {'title': '安装', 'uidt': 'Number'},
    {'title': '转化率', 'uidt': 'Decimal'},
    {'title': '单价', 'uidt': 'Decimal'},
    {'title': '首充用户', 'uidt': 'Number'},
    {'title': '首充率', 'uidt': 'Decimal'},
    {'title': '首日回收', 'uidt': 'Decimal'},
    {'title': '单个付费用户成本', 'uidt': 'Decimal'},
    {'title': '首日ROI', 'uidt': 'Decimal'},
    {'title': '累计付费', 'uidt': 'Decimal'},
    {'title': '累计ROI', 'uidt': 'Decimal'},
    {'title': '次留', 'uidt': 'Decimal'},
    {'title': '时长', 'uidt': 'Decimal'},
    {'title': '分辨率', 'uidt': 'SingleLineText'},
    {'title': '备注', 'uidt': 'LongText'},
])

# Table 2: AI分析结果
T2 = create_table('AI分析结果', [
    {'title': '素材名称', 'uidt': 'SingleLineText'},
    {'title': '游戏名称', 'uidt': 'SingleLineText'},
    {'title': '素材类型', 'uidt': 'SingleLineText'},
    {'title': 'Hook内容', 'uidt': 'LongText'},
    {'title': 'Hook类型', 'uidt': 'SingleLineText'},
    {'title': '人物', 'uidt': 'LongText'},
    {'title': 'UI元素', 'uidt': 'LongText'},
    {'title': '特效', 'uidt': 'LongText'},
    {'title': '色彩', 'uidt': 'SingleLineText'},
    {'title': '字幕', 'uidt': 'SingleLineText'},
    {'title': '节奏', 'uidt': 'LongText'},
    {'title': 'BGM', 'uidt': 'SingleLineText'},
    {'title': '核心卖点', 'uidt': 'LongText'},
    {'title': 'hook_策略', 'uidt': 'SingleLineText'},
    {'title': '角色元素', 'uidt': 'SingleLineText'},
    {'title': '场景元素', 'uidt': 'SingleLineText'},
    {'title': '制作方式', 'uidt': 'SingleLineText'},
    {'title': '叙事模式', 'uidt': 'SingleLineText'},
    {'title': '画面风格', 'uidt': 'SingleLineText'},
    {'title': '目标受众', 'uidt': 'SingleLineText'},
    {'title': '情绪触发', 'uidt': 'SingleLineText'},
    {'title': '营销策略', 'uidt': 'LongText'},
    {'title': '可优化方向', 'uidt': 'LongText'},
    {'title': '标签状态', 'uidt': 'SingleLineText'},
    {'title': 'AI置信度', 'uidt': 'Decimal'},
    {'title': 'AI原始输出', 'uidt': 'LongText'},
    {'title': '分析时间', 'uidt': 'DateTime'},
    {'title': '人工批注', 'uidt': 'LongText'},
])

# Table 3: 逐帧拆解
T3 = create_table('逐帧拆解', [
    {'title': '素材名称', 'uidt': 'SingleLineText'},
    {'title': '游戏名称', 'uidt': 'SingleLineText'},
    {'title': '时间轴', 'uidt': 'SingleLineText'},
    {'title': '画面内容', 'uidt': 'LongText'},
    {'title': '画面元素', 'uidt': 'LongText'},
    {'title': '文案/字幕', 'uidt': 'LongText'},
    {'title': '音效', 'uidt': 'SingleLineText'},
    {'title': '目的', 'uidt': 'LongText'},
    {'title': '可借鉴点', 'uidt': 'LongText'},
])

# Table 4: 标签字典
T4 = create_table('标签字典', [
    {'title': '游戏名称', 'uidt': 'SingleLineText'},
    {'title': '维度', 'uidt': 'SingleLineText'},
    {'title': '标签值', 'uidt': 'SingleLineText'},
    {'title': '说明', 'uidt': 'LongText'},
    {'title': '示例素材', 'uidt': 'SingleLineText'},
])

# Table 5: 效果聚合
T5 = create_table('效果聚合', [
    {'title': '游戏名称', 'uidt': 'SingleLineText'},
    {'title': '分析维度', 'uidt': 'SingleLineText'},
    {'title': '标签值', 'uidt': 'SingleLineText'},
    {'title': '素材数', 'uidt': 'Number'},
    {'title': '平均花费', 'uidt': 'Decimal'},
    {'title': '平均单价', 'uidt': 'Decimal'},
    {'title': '平均首日ROI', 'uidt': 'Decimal'},
    {'title': '平均累计ROI', 'uidt': 'Decimal'},
    {'title': '平均次留', 'uidt': 'Decimal'},
    {'title': '结论', 'uidt': 'SingleLineText'},
])

# Delete test table
test_id = 'mifrxwb07uqsr4c'
r = requests.delete(f'{BASE}/api/v2/meta/tables/{test_id}', headers=H)
print(f'\n  Deleted test table: {r.status_code}')

print(f'\n=== Done ===')
print(f'素材主表: {T1}')
print(f'AI分析结果: {T2}')
print(f'逐帧拆解: {T3}')
print(f'标签字典: {T4}')
print(f'效果聚合: {T5}')
