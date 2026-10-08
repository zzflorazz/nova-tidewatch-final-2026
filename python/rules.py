# TideWatch 统一判级规则
# 与 web/js/rules.js 保持完全一致
# 按顺序判断，水位维度优先


def judge(water_level, turbidity):
    """
    统一环境判级函数
    :param water_level: 水位（米）
    :param turbidity: 浊度（NTU）
    :return: dict {status, advice}
    """
    # ① 水位 ≥ 8.5 → 洪水预警
    if water_level >= 8.5:
        return {'status': '洪水预警', 'advice': '立即启动防汛预案，开闸泄洪'}
    # ② 否则 水位 ≥ 7.0 → 高水位警戒
    if water_level >= 7.0:
        return {'status': '高水位警戒', 'advice': '加强监测，做好泄洪准备'}
    # ③ 否则 浊度 ≥ 60 → 水质浑浊
    if turbidity >= 60:
        return {'status': '水质浑浊', 'advice': '投加净水剂，加强水质监测'}
    # ④ 其余 → 正常
    return {'status': '正常', 'advice': '环境正常，持续监测'}


# 水流等级说明
FLOW_LEVELS = {
    0: '缓流',
    1: '平稳',
    2: '湍急',
    3: '湍流'
}

# 状态颜色映射
STATUS_COLORS = {
    '正常': '#52c41a',
    '高水位警戒': '#faad14',
    '洪水预警': '#f5222d',
    '水质浑浊': '#13c2c2'
}

# 状态风险权重（用于优先关注计算）
STATUS_WEIGHTS = {
    '正常': 0,
    '水质浑浊': 2,
    '高水位警戒': 3,
    '洪水预警': 4
}

# 回归测试数据
TEST_CASES = [
    {'water_level': 4.5, 'turbidity': 20, 'expected': '正常'},
    {'water_level': 7.6, 'turbidity': 20, 'expected': '高水位警戒'},
    {'water_level': 9.0, 'turbidity': 20, 'expected': '洪水预警'},
    {'water_level': 4.5, 'turbidity': 75, 'expected': '水质浑浊'}
]


def run_tests():
    """运行回归测试"""
    passed = 0
    for i, tc in enumerate(TEST_CASES):
        result = judge(tc['water_level'], tc['turbidity'])
        ok = result['status'] == tc['expected']
        if ok:
            passed += 1
        status = '✓' if ok else '✗'
        print(f"测试 {i+1}: waterLevel={tc['water_level']}, turbidity={tc['turbidity']} → {result['status']} (期望 {tc['expected']}) {status}")
    print(f"\n通过 {passed}/{len(TEST_CASES)}")
    return passed == len(TEST_CASES)


if __name__ == '__main__':
    run_tests()
