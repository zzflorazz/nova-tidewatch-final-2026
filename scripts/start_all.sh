#!/bin/bash
# TideWatch 一键启动全链路（在任意目录、任意终端执行均可，自动切到仓库根目录）
# 启动：Broker（复用或启动）+ shared_state(:8000) + event_manager(:8001) + 水文节点 + web(:8080)
# 日志：/tmp/tidewatch/*.log ｜ 停止：bash scripts/stop_all.sh

cd "$(dirname "$0")/.." || exit 1
LOG_DIR=/tmp/tidewatch
mkdir -p "$LOG_DIR"

port_busy() { lsof -nP -i :"$1" >/dev/null 2>&1; }
wait_port() { for _ in $(seq 1 20); do port_busy "$1" && return 0; sleep 0.5; done; return 1; }

echo "==> TideWatch 一键启动（仓库目录：$(pwd)）"
echo ""

# 1. MQTT Broker
if port_busy 1883; then
  echo "✓ Broker 已在运行（:1883）"
else
  echo "→ 启动 mosquitto ..."
  if command -v brew >/dev/null 2>&1; then
    brew services start mosquitto >/dev/null 2>&1 || mosquitto -c scripts/mosquitto.conf -d
  else
    mosquitto -c scripts/mosquitto.conf -d
  fi
  wait_port 1883 && echo "✓ Broker 已启动（:1883）" || echo "✗ Broker 启动失败，请检查 mosquitto 安装"
fi

# 2. 共享状态服务
if port_busy 8000; then
  echo "✓ shared_state 已在运行（:8000）"
else
  nohup python3 -u python/shared_state.py > "$LOG_DIR/shared_state.log" 2>&1 &
  echo "→ shared_state 启动中（:8000，日志 $LOG_DIR/shared_state.log）"
fi

# 3. 事件管理器
if port_busy 8001; then
  echo "✓ event_manager 已在运行（:8001）"
else
  nohup python3 -u python/event_manager.py > "$LOG_DIR/event_manager.log" 2>&1 &
  echo "→ event_manager 启动中（:8001，日志 $LOG_DIR/event_manager.log）"
fi

# 4. 水文节点模拟器（3 断面）
if pgrep -f "python/mqtt_node.py" >/dev/null 2>&1; then
  echo "✓ 水文节点已在运行"
else
  nohup python3 -u python/mqtt_node.py --interval 3 > "$LOG_DIR/mqtt_node.log" 2>&1 &
  echo "→ 水文节点启动中（3 断面，间隔 3s，日志 $LOG_DIR/mqtt_node.log）"
fi

# 5. Web 静态服务
if port_busy 8080; then
  echo "✓ web 服务已在运行（:8080）"
else
  nohup python3 -u -m http.server 8080 --directory web > "$LOG_DIR/web.log" 2>&1 &
  echo "→ web 服务启动中（:8080，日志 $LOG_DIR/web.log）"
fi

sleep 1.5
echo ""
echo "===== 启动结果 ====="
port_busy 1883 && echo "  Broker              :1883  ✓" || echo "  Broker              :1883  ✗"
port_busy 8000 && echo "  共享状态服务         :8000  ✓" || echo "  共享状态服务         :8000  ✗"
port_busy 8001 && echo "  事件管理器           :8001  ✓" || echo "  事件管理器           :8001  ✗"
port_busy 8080 && echo "  Web 服务            :8080  ✓" || echo "  Web 服务            :8080  ✗"
echo ""
echo "浏览器打开："
echo "  监测台   http://localhost:8080/index.html"
echo "  移动端   http://localhost:8080/mobile.html"
echo "  3D       http://localhost:8080/3d.html"
echo "  多模态   http://localhost:8080/multimodal.html"
echo "  对照页   http://localhost:8080/compare.html"
echo ""
echo "停止全部组件：bash scripts/stop_all.sh"
