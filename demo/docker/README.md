# SM2/SM3/SM4 + DMXAPI 四 Agent 本地演示

本目录整合原 ATP Hackathon demo。Travel、Search、Rates、Bill 使用 Pi 0.80.6 调用配置的模型；域间消息默认使用 TLCP（SM2 双证书、SM4-CBC、SM3），载荷另以 SM4 加密，并使用 SM2 封装逐消息会话密钥、SM3 完整性校验和 SM2/SM3 ATK 签名。库存、价格和支付仍为模拟业务。

## 配置与启动

1. 将 `.env.example` 复制为 `.env`，填写模型 API Key。示例使用 DMXAPI 的 qwen3.7-plus。
2. Linux/WSL 需要 Docker、Compose v2、Node.js 22.19+、npm、openssl、curl、jq。
3. 在本目录执行 `bash scripts/setup.sh`，然后访问 http://localhost:8080/ 。
4. Windows + WSL 用户参见 [RUN_WINDOWS.md](RUN_WINDOWS.md)。
5. `python scripts/check-model.py` 检查 DMXAPI 流式工具调用；会产生少量模型费用。
6. `bash scripts/test-demo-controller.sh` 检查四 Agent、任务结果和抓包证据；会清空演示消息与会话。
7. `bash scripts/test-sm2-transfer.sh` 检查真实域间 SM2 验签、篡改/重放拒绝及三域重启后密钥保持不变；测试会短暂重启域服务器。
8. `bash scripts/test-sm4-tlcp.sh` 检查远端数据库仅保存 SM4 密文、SM2 会话密钥封装、接收端解密、TLCP 密码套件以及目标白名单。

首次启动由 ATPServer 生成 SM2 密钥；重启加载已有密钥，损坏或缺失半对密钥应报错。`scripts/update-atk.sh` 将 SM2 公钥写入本地 DNS 的 `k=sm2` 记录。构建后端使用仓库根目录源码，不依赖外部 atp_bundle 路径。

仓库中的 DNS 公钥是演示样例；每台机器首次启动后必须执行更新脚本，使其匹配本机密钥。服务 Agent 在域服务器短暂断线时自动重试接收，保留本进程的接收游标。

若 ISC DNS 镜像无法下载，可使用 `bind/Dockerfile` 构建 Debian BIND9，并通过 `compose.local-dns.yml` 覆盖 DNS 镜像，详见 Windows 说明。

## 结构

- `agents/pi/runtime.mjs`：四个角色的模型会话与工具。
- `agents/pi_adapter.py`：模型工具和 Python ATP SDK 的桥接。
- `demo/controller.py`：对话 API、运行记录、容器管理与抓包。
- `demo/web`：React 前端源码；`demo/static`：已构建静态资源。
- `tlcp-gateway`：Docker 内部国密网关，负责三域间 TLCP 握手和 HTTP 转发。
- `bind`：本地三域 DNS；`scripts`：构建、证书、DNS 更新和测试。

## 边界

SM2/SM3 保护消息真实性与完整性，SM4 保护跨域消息载荷；接收域 ATP Server 在投递给本地域 Agent 前解密。三域之间默认经过 TLCP 网关，使用 SM2 签名/加密双证书、SM4-CBC 和 SM3；设置 `ATP_TRANSPORT_MODE=tls` 可切回原有 TLS 1.3 兼容模式。本地 Agent 到所属 ATP Server、浏览器到演示控制器仍使用原有 HTTP/TLS 通道，不应表述为所有本地链路均为 TLCP。

TLCP 网关端口只在 Docker 内部网络开放；目标域必须出现在 `ATP_TLCP_PEERS` 白名单。网关到本域 ATP 后端使用经过本地 CA 验证的 TLS 1.3，未关闭证书校验。

抓包信号只证明 DNS/TCP/TLS 等网络活动，不能独立证明国密验签成功。应同时核对服务器公钥记录、消息算法及接收/拒绝结果。

密钥、`.env`、证书、数据库、日志和 node_modules 不提交。旧 `travel_agent.py` 与 `scene_*` 为兼容演示路径，浏览器默认使用 Pi 运行时。
