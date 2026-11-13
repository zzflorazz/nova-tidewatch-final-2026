# 本地浏览器依赖

用于断网环境下的静态监测页面。下载日期：2026-11-13。原始文件未改写。

| 文件 | 来源 | 字节数 | SHA256 |
|---|---|---:|---|
| three.min.js | https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js | 603445 | 9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2 |
| OrbitControls.js | https://unpkg.com/three@0.128.0/examples/js/controls/OrbitControls.js | 26375 | 02bb4ade710f3e607329e37a21f098bc3ac70eb6e33daf8a65e79f4db785e7b2 |
| mqtt.min.js | https://unpkg.com/mqtt@5.10.4/dist/mqtt.min.js | 310735 | 0d5d035815d2a462e6edf0f86ab91f7a5d563a36b40e9760e4ee452618f7c45e |
| LICENSE.three.txt | https://unpkg.com/three@0.128.0/LICENSE | 1081 | 7dddf7c5b8fd10ee654db8857d75d104b5557889aa5a91fc4ca545ea7c07062f |
| LICENSE.mqtt.txt | https://unpkg.com/mqtt@5.10.4/LICENSE.md | 1271 | 81b51ef8ac2a8ac8f111ed34ab1a32d63e7f4f7de586bd65f5510fb1ba3a89eb |

Three.js 与 OrbitControls 同属 three@0.128.0（r128）；MQTT.js 固定为 5.10.4。许可证原文随文件保留。

下载记录：jsDelivr 的 OrbitControls 及 Three.js LICENSE 请求连接被重置，改用 unpkg 的同版本资源成功；cdnjs 的 Three.js 与 unpkg 的 MQTT.js 首次 SSL 超时，重试后成功。

离线可用指本机 FastAPI 服务仍在运行、无外网且 MQTT 不可连接时仍能加载静态页面和 3D 场景；不伪造实时观测。
