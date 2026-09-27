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

## 模型状态与任务校验

本地 Pi 进程启动仅显示“运行环境已启动”。模型首次实际请求成功后才显示“模型已连接”和最近成功时间；这表示已验证的请求，不承诺之后的网络始终可用。无需刷新时重复发送付费探测。密钥、模型权限、额度、限流、网络和超时错误以中文显示，页面不展示服务商原始错误。

Travel 按任务 ID、上下文 ID、请求 nonce 和发送方验证回复。搜索和付款结果由业务工具产生结构化数据；收齐三条不同事件序号的价格后才允许模拟付款。完整预订成功要求五条相关回复（搜索、三条价格、付款）和批准的模拟付款结果。只搜索或只订阅的任务明确说明尚未付款。

控制器执行阶段总上限为 180 秒，Pi 默认在 175 秒停止；每次模型请求最多 60 秒，单阶段 ATP 等待最多 60 秒/12 次内部轮询，总工具调用最多 24 次。HTTP 错误不自动重试；限流提示稍后重试，避免扩大费用和付款歧义。已发送的跨域请求无法撤回，超时不等于付款一定未执行，系统不会自动重复发送付款。

每次运行保存 `traces/<run_id>.report.json`，包含模型、耗时、工具次数、任务证据、结果和错误码。可通过 `/api/runs/<run_id>/report` 获取。完整回归脚本另保存报告及会话快照到 `traces/regressions`；这些运行数据不提交。

离线异常回归（不读取真实密钥、不调用收费接口）：

```bash
cd agents/pi
npm ci
node --test guards.test.mjs runtime.test.mjs
```

默认使用 `python3`；Windows 在运行测试前将 `ATP_PYTHON` 设置为本机 Python 可执行文件路径。仓库根目录安装演示依赖 `pip install docker` 后，可执行 `python -m pytest tests/test_demo_model_runtime.py` 验证控制器取消、状态与报告。

真实模型完整回归：`bash scripts/test-demo-controller.sh`。脚本会重置演示会话并产生模型调用费用，验证五条关联回复、最低价格、模拟付款结果和 TLCP 抓包证据。测试超时 240 秒包含结果/抓包收集时间，不改变单任务 180 秒执行限制。

## 代码结构

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

### 偶发空响应恢复

正常结束但没有文字、或授权流程尚未完成时，运行时最多补充两轮继续请求；共用原任务截止时间及工具调用预算。认证、额度、网络错误不会按空响应重试。已有发送记录和关联消息保留，不重新发送已提交的付款。服务 Agent 已发送业务回执时无需强求模型再生成一段文字；Travel 已有有效付款证据时由程序生成结果。持续空响应仍明确失败。页面“尚无任务记录”表示尚未观察到该角色活动，不是模型健康判断。
