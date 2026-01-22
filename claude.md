# Docker MCP Gateway

统一管理多个 Docker MCP 容器的网关服务。

## 核心功能

- **统一入口**: 对外暴露单一地址，路径路由到不同容器
- **路径格式**: `/mcp/{server_name}` → 对应容器的 `/mcp` 端点
- **Docker 管理**: 解析 `docker run` 命令，自动创建/管理容器
- **Web Dashboard**: 可视化管理界面
- **健康检查**: 自动监控容器状态
- **日志查看**: 实时查看容器日志
- **访问统计**: 记录各容器访问次数

## 架构

```
                    ┌──────────────────────────────────┐
                    │      Docker MCP Gateway          │
                    │      http://0.0.0.0:8080         │
                    └──────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
    /mcp/taoke               /mcp/other               /mcp/xxx
          │                        │                        │
   ┌──────┴──────┐          ┌──────┴──────┐          ┌──────┴──────┐
   │  taoke-mcp  │          │  other-mcp  │          │   xxx-mcp   │
   │  container  │          │  container  │          │  container  │
   └─────────────┘          └─────────────┘          └─────────────┘
```

## 技术栈

- **FastAPI**: Web 框架
- **docker-py**: Docker SDK
- **httpx**: 异步 HTTP 客户端（反向代理）
- **WebSocket**: 支持 WebSocket 透传

## 快速启动

```bash
cd /Users/xiexinfa/demo/taoke_docker
uv sync
uv run docker-mcp-gateway
```

## 服务器部署

### 本地部署（在服务器上执行）

```bash
./deploy.sh start     # 启动
./deploy.sh stop      # 停止
./deploy.sh restart   # 重启
./deploy.sh status    # 状态
./deploy.sh deploy    # 更新代码并重启
./deploy.sh health    # 健康检查
./deploy.sh logs      # 查看日志
```

### 远程部署（从本地部署到服务器）

```bash
# 1. 配置（首次）
cp .env.remote.example .env.remote
# 编辑 .env.remote

# 2. 一键部署
./deploy_remote.sh           # 直接运行即可

# 其他命令
./deploy_remote.sh status    # 远程状态
./deploy_remote.sh logs      # 远程日志
./deploy_remote.sh ssh       # 登录服务器
```

## 配置

配置文件: `config/containers.yaml`

环境变量配置: `.env` (参考 `.env.example`)

环境变量:
- `HOST`: 绑定地址 (默认 0.0.0.0)
- `PORT`: 监听端口 (默认 18082)
- `CONFIG_DIR`: 配置目录 (默认 ./config)
- `DATA_DIR`: 数据目录 (默认 ./data)
- `LOG_LEVEL`: 日志级别 (默认 INFO)

## API 端点

- `GET /` - Web Dashboard
- `GET /api/status` - 网关状态
- `GET /api/containers` - 容器列表
- `POST /api/containers` - 创建容器（支持 docker run 命令解析）
- `DELETE /api/containers/{name}` - 删除容器
- `POST /api/containers/{name}/start` - 启动容器
- `POST /api/containers/{name}/stop` - 停止容器
- `GET /api/containers/{name}/logs` - 容器日志
- `ANY /mcp/{server_name}/*` - MCP 代理端点

## 目录结构

```
docker_mcp_gateway/
├── src/docker_mcp_gateway/
│   ├── __init__.py
│   ├── __main__.py          # 入口
│   ├── app.py               # FastAPI 应用
│   ├── docker_manager.py    # Docker 容器管理
│   ├── docker_parser.py     # docker run 命令解析
│   ├── proxy.py             # 反向代理
│   ├── config.py            # 配置管理
│   └── models.py            # 数据模型
├── web/                     # 前端静态文件
├── config/                  # 配置文件
├── data/                    # 持久化数据
├── logs/                    # 日志目录（自动创建）
├── backups/                 # 配置备份（自动创建）
├── deploy.sh                # 本地部署脚本
├── deploy_remote.sh         # 远程部署脚本（一键部署）
├── .env.example             # 本地环境变量示例
└── .env.remote.example      # 远程部署配置示例
```
