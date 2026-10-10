// TideWatch 浏览器端 MQTT 客户端封装（支持多 topic 订阅）
// 依赖：页面中先引入 mqtt.js
//   <script src="https://unpkg.com/mqtt/dist/mqtt.min.js"></script>
// 暴露全局对象 TideWatchMQTT：
//   connect() / subscribe(cb) / subscribeTopic(topic, cb) / publish(reachId, data) /
//   publishTopic(topic, data) / isConnected() / onConnectionChange(cb)

const BROKER_WS_URL = 'ws://localhost:8083/mqtt';
const TOPIC_SUBSCRIBE = 'tidewatch/+/data';
const TOPIC_TEMPLATE = 'tidewatch/{reachId}/data';

const TideWatchMQTT = (() => {
  let client = null;
  let connected = false;
  const topicCallbacks = {};    // topic 过滤器 -> [回调...]
  const subscribedTopics = {};  // topic 过滤器 -> 是否已订阅
  const statusCallbacks = [];

  function onConnectionChange(callback) {
    if (typeof callback === 'function') statusCallbacks.push(callback);
  }

  function _emitStatus(isConnected) {
    statusCallbacks.forEach((cb) => {
      try {
        cb(isConnected);
      } catch (e) {
        console.error('[TideWatchMQTT] 状态回调出错:', e);
      }
    });
  }

  /** MQTT topic 通配符匹配（+ 匹配一层，# 匹配剩余所有层） */
  function _topicMatches(filter, topic) {
    const f = filter.split('/');
    const t = topic.split('/');
    for (let i = 0; i < f.length; i++) {
      if (f[i] === '#') return true;
      if (i >= t.length) return false;
      if (f[i] !== '+' && f[i] !== t[i]) return false;
    }
    return f.length === t.length;
  }

  /** 重连后恢复所有已订阅 topic */
  function _resubscribeAll() {
    Object.keys(subscribedTopics).forEach((topic) => {
      if (!subscribedTopics[topic]) return;
      client.subscribe(topic, (err) => {
        if (err) console.error(`[TideWatchMQTT] 重连后重新订阅 ${topic} 失败:`, err.message || err);
        else console.log(`[TideWatchMQTT] ⟳ 已恢复订阅 ${topic}`);
      });
    });
  }

  function connect() {
    if (typeof mqtt === 'undefined') {
      console.error('[TideWatchMQTT] 未找到 mqtt.js，请先引入 https://unpkg.com/mqtt/dist/mqtt.min.js');
      return null;
    }
    if (client) return client;

    client = mqtt.connect(BROKER_WS_URL, {
      clientId: 'tidewatch-web-' + Math.random().toString(16).slice(2, 8),
      clean: true,
      reconnectPeriod: 2000,
      connectTimeout: 8000,
    });

    client.on('connect', () => {
      connected = true;
      console.log(`[TideWatchMQTT] ✓ 已连接 ${BROKER_WS_URL}`);
      _emitStatus(true);
      _resubscribeAll();
    });

    client.on('reconnect', () => {
      console.log('[TideWatchMQTT] ⟳ 重连中 ...');
    });

    client.on('close', () => {
      if (connected) console.log('[TideWatchMQTT] ✗ 连接已断开');
      connected = false;
      _emitStatus(false);
    });

    client.on('offline', () => {
      console.log('[TideWatchMQTT] ✗ 已离线');
      connected = false;
      _emitStatus(false);
    });

    client.on('error', (err) => {
      console.error('[TideWatchMQTT] 连接错误:', err.message || err);
    });

    client.on('message', (topic, payload) => {
      let data;
      try {
        data = JSON.parse(payload.toString());
      } catch (e) {
        console.error(`[TideWatchMQTT] ✗ 消息 JSON 解析失败 (${topic}):`, e.message,
                      '| 原始内容:', payload.toString().slice(0, 120));
        return;
      }
      Object.keys(topicCallbacks).forEach((filter) => {
        if (!_topicMatches(filter, topic)) return;
        topicCallbacks[filter].forEach((cb) => {
          try {
            cb(data, topic);
          } catch (e) {
            console.error('[TideWatchMQTT] 回调执行出错:', e);
          }
        });
      });
    });

    return client;
  }

  /** 订阅指定 topic（或带通配符的过滤器），为每个收到的消息调用 callback(data, topic) */
  function subscribeTopic(topic, callback) {
    if (!client) connect();
    if (!client) return;
    if (typeof callback === 'function') {
      (topicCallbacks[topic] = topicCallbacks[topic] || []).push(callback);
    }
    if (!subscribedTopics[topic]) {
      client.subscribe(topic, (err) => {
        if (err) {
          console.error(`[TideWatchMQTT] 订阅 ${topic} 失败:`, err.message || err);
        } else {
          subscribedTopics[topic] = true;
          console.log(`[TideWatchMQTT] ✓ 已订阅 ${topic}`);
        }
      });
    }
  }

  /** 默认订阅断面数据 topic tidewatch/+/data */
  function subscribe(callback) {
    subscribeTopic(TOPIC_SUBSCRIBE, callback);
  }

  /** 发布到指定 topic */
  function publishTopic(topic, data) {
    if (!client || !connected) {
      console.error('[TideWatchMQTT] ✗ 未连接 Broker，无法发布');
      return false;
    }
    client.publish(topic, JSON.stringify(data));
    console.log(`[TideWatchMQTT] → 已发布 ${topic}:`, data);
    return true;
  }

  /** 发布断面数据到 tidewatch/{reachId}/data */
  function publish(reachId, data) {
    return publishTopic(TOPIC_TEMPLATE.replace('{reachId}', reachId), data);
  }

  function isConnected() {
    return connected;
  }

  return { connect, subscribe, subscribeTopic, publish, publishTopic, isConnected, onConnectionChange };
})();

if (typeof window !== 'undefined') {
  window.TideWatchMQTT = TideWatchMQTT;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { TideWatchMQTT, BROKER_WS_URL, TOPIC_SUBSCRIBE, TOPIC_TEMPLATE };
}
