#!/usr/bin/env python3
# 训练 TideWatch 轻量 ML 模型（IsolationForest）并保存
# 从 data/history.csv 读取历史 waterLevel / turbidity 训练，输出 4 组回归测试数据的 ML 判断
#
# 用法: python3 python/train_ml.py

import os

import joblib

from analyze import BASE_DIR, load_data, resolve_data_path, train_isolation_forest

MODEL_PATH = os.path.join(BASE_DIR, 'python', 'ml_model.joblib')

# 统一回归测试数据（与 rules 保持一致）
REGRESSION_CASES = [(4.5, 20), (7.6, 20), (9.0, 20), (4.5, 75)]


def main():
    data_path = resolve_data_path()
    print(f"读取训练数据: {os.path.relpath(data_path, BASE_DIR)}")
    df = load_data(data_path)
    print(f"样本数: {len(df)}（特征: waterLevel, turbidity）")

    model = train_isolation_forest(df, contamination=0.1, random_state=42)
    joblib.dump(model, MODEL_PATH)
    print(f"模型已保存: {os.path.relpath(MODEL_PATH, BASE_DIR)}")
    print("参数: IsolationForest(n_estimators=100, contamination=0.1, random_state=42)")
    print()

    print("===== 4 组回归测试数据的 ML 判断 =====")
    print("（anomaly_score 越小越异常；is_anomaly=True 表示与历史分布明显不同）")
    X = [[wl, tub] for wl, tub in REGRESSION_CASES]
    scores = model.decision_function(X)
    preds = model.predict(X)
    for (wl, tub), score, pred in zip(REGRESSION_CASES, scores, preds):
        is_anomaly = pred == -1
        verdict = '与历史分布明显不同，需关注' if is_anomaly else '接近历史常态'
        print(f"waterLevel={wl:>4}, turbidity={tub:>3} → "
              f"anomaly_score={score:+.4f}, is_anomaly={is_anomaly} → ML判断: {verdict}")


if __name__ == '__main__':
    main()
