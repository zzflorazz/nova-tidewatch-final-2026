// TideWatch 统一判级规则
// 所有模块（Web、Python、Dashboard、小程序）必须使用同一套规则
// 按顺序判断，水位维度优先

/**
 * 统一环境判级函数
 * @param {number} waterLevel - 水位（米）
 * @param {number} turbidity - 浊度（NTU）
 * @returns {{status: string, advice: string}}
 */
function judge(waterLevel, turbidity) {
  // ① 水位 ≥ 8.5 → 洪水预警
  if (waterLevel >= 8.5) {
    return { status: '洪水预警', advice: '立即启动防汛预案，开闸泄洪' };
  }
  // ② 否则 水位 ≥ 7.0 → 高水位警戒
  if (waterLevel >= 7.0) {
    return { status: '高水位警戒', advice: '加强监测，做好泄洪准备' };
  }
  // ③ 否则 浊度 ≥ 60 → 水质浑浊
  if (turbidity >= 60) {
    return { status: '水质浑浊', advice: '投加净水剂，加强水质监测' };
  }
  // ④ 其余 → 正常
  return { status: '正常', advice: '环境正常，持续监测' };
}

// 水流等级说明
const FLOW_LEVELS = {
  0: '缓流',
  1: '平稳',
  2: '湍急',
  3: '湍流'
};

// 状态颜色映射
const STATUS_COLORS = {
  '正常': '#52c41a',
  '高水位警戒': '#faad14',
  '洪水预警': '#f5222d',
  '水质浑浊': '#13c2c2'
};

// 状态风险权重（用于优先关注计算）
const STATUS_WEIGHTS = {
  '正常': 0,
  '水质浑浊': 2,
  '高水位警戒': 3,
  '洪水预警': 4
};

// 回归测试数据
const TEST_CASES = [
  { waterLevel: 4.5, turbidity: 20, expected: '正常' },
  { waterLevel: 7.6, turbidity: 20, expected: '高水位警戒' },
  { waterLevel: 9.0, turbidity: 20, expected: '洪水预警' },
  { waterLevel: 4.5, turbidity: 75, expected: '水质浑浊' }
];

// 单元测试
function runTests() {
  let passed = 0;
  TEST_CASES.forEach((tc, i) => {
    const result = judge(tc.waterLevel, tc.turbidity);
    const ok = result.status === tc.expected;
    if (ok) passed++;
    console.log(`测试 ${i + 1}: waterLevel=${tc.waterLevel}, turbidity=${tc.turbidity} → ${result.status} (期望 ${tc.expected}) ${ok ? '✓' : '✗'}`);
  });
  console.log(`\n通过 ${passed}/${TEST_CASES.length}`);
  return passed === TEST_CASES.length;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { judge, FLOW_LEVELS, STATUS_COLORS, STATUS_WEIGHTS, TEST_CASES, runTests };
}
