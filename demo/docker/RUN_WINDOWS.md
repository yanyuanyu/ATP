# Windows + WSL 启动说明

当前开发环境使用 Ubuntu 内已有的 Docker Engine，无需额外安装 Docker Desktop。
需要 Windows Node.js 22.19+、npm，以及 Ubuntu 内的 Docker、Compose v2、openssl、curl 和 jq。

## 模型配置

在本目录 `.env` 中配置 DMXAPI 地址、模型 ID 和密钥。不要提交 `.env`。
`python scripts/check-model.py` 会发送两次小型付费请求，检查流式工具调用和结果回传，不输出密钥。

## 构建

在 PowerShell 中执行 `scripts/build-windows.ps1`（可用 `-Distribution` 指定 WSL 发行版）。
脚本按自身位置寻找项目，在 Windows 构建前端，在 WSL 构建 Linux 镜像。
它不依赖固定盘符或解压目录，首次构建需要下载依赖和基础镜像。

Python 依赖下载缓慢时可使用公开镜像：
`scripts/build-windows.ps1 -PipIndexUrl https://pypi.tuna.tsinghua.edu.cn/simple`。
Linux/WSL 则先执行 `export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`。
此参数仅供构建下载依赖使用，请勿在 URL 中填写账号或令牌。

## 启动

在 Ubuntu 终端中进入本项目的 docker 目录，执行：

```bash
docker compose config --quiet
# 首次部署生成本机测试证书；已有运行中的部署不要随意重新生成。
bash scripts/gen-certs.sh
docker compose up -d
```

确认三个 server 容器均已完成初始化后执行：

```bash
bash scripts/update-atk.sh
```

从 Windows 浏览器打开 http://localhost:8080/ 。
使用 WSL 内的 Docker Engine 时，演示期间保留 Ubuntu 终端会话；可运行
`docker compose logs -f --tail 20` 查看日志，避免 WSL 空闲退出使容器中断。
执行 `bash scripts/test-demo-controller.sh` 进行四 Agent 回归；该脚本会重置演示会话和消息数据，并产生模型费用。
`docker compose stop` 停止项目；`docker compose start` 重新启动已有容器。
修改 `.env` 后使用 `docker compose up -d` 重建受影响容器，单纯 restart 不会更新环境变量。

## 官方 DNS 镜像下载失败时

本次机器使用以下可选方案，从 Debian 软件源构建 BIND9（版本与原 ISC 9.18 镜像不同）：

```bash
docker build -t atp-hackathon-dns:local -f bind/Dockerfile bind
export COMPOSE_FILE=docker-compose.yml:compose.local-dns.yml
docker compose up -d
bash scripts/update-atk.sh
```

在同一终端执行后续测试与管理命令，保留 `COMPOSE_FILE`，以继续使用本地 DNS 镜像。

## 范围

模型接口测试成功不代表 ATP 完整链路成功，也不代表已经完成国密改造。
这是本地演示环境，包含模拟业务、测试凭据、跳过证书验证的 Agent 配置及 Docker socket 管理接口。
打包提交时排除个人密钥、虚拟环境、node_modules 和无关日志；另行准备经过验证的镜像包。
