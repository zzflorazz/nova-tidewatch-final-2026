#!/bin/bash
# TideWatch 停止后台组件（由 start_all.sh 启动的 4 个组件；不动 Broker）

cd "$(dirname "$0")/.." || exit 1

for pat in "python/shared_state.py" "python/event_manager.py" "python/mqtt_node.py" "http.server 8080"; do
  pids=$(pgrep -f "$pat" 2>/dev/null)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null
    echo "已停止: ${pat}（PID: ${pids}）"
  else
    echo "未运行: ${pat}"
  fi
done
echo "（Broker 未动：如需停止 mosquitto 服务可执行 brew services stop mosquitto）"
