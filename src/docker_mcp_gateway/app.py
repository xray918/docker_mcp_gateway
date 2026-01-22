"""FastAPI 应用主模块"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ConfigManager
from .docker_manager import DockerManager
from .models import GatewayStatus
from .proxy import cleanup_proxy, get_proxy_client, get_websocket_proxy

logger = logging.getLogger(__name__)

# 全局变量
_docker_manager: DockerManager | None = None
_gateway_status: GatewayStatus | None = None


# ==================== Pydantic 模型 ====================

class CreateContainerRequest(BaseModel):
    """创建容器请求"""
    docker_command: str  # docker run 命令


class ContainerResponse(BaseModel):
    """容器响应"""
    name: str
    status: str
    image: str
    internal_port: int
    host_port: int | None = None  # 主机端口映射
    external_path: str
    internal_url: str | None = None  # 内部访问 URL
    health_status: str = "unknown"
    total_requests: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    error_message: str | None = None


class StatusResponse(BaseModel):
    """状态响应"""
    start_time: str
    uptime_seconds: float
    total_containers: int
    running_containers: int
    total_requests: int
    memory_usage_mb: float


# ==================== 生命周期 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global _docker_manager, _gateway_status
    
    logger.info("Docker MCP Gateway 启动中...")
    
    # 初始化配置管理器
    config_dir = os.getenv("CONFIG_DIR", "./config")
    data_dir = os.getenv("DATA_DIR", "./data")
    config_manager = ConfigManager(config_dir=config_dir, data_dir=data_dir)
    
    # 初始化 Docker 管理器
    _docker_manager = DockerManager(config_manager)
    await _docker_manager.initialize()
    
    # 初始化网关状态
    _gateway_status = GatewayStatus(start_time=datetime.now())
    
    logger.info("Docker MCP Gateway 启动完成")
    
    yield
    
    # 清理
    logger.info("Docker MCP Gateway 关闭中...")
    
    if _docker_manager:
        await _docker_manager.cleanup()
    
    await cleanup_proxy()
    
    logger.info("Docker MCP Gateway 已关闭")


# ==================== 创建应用 ====================

def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title="Docker MCP Gateway",
        description="统一管理多个 Docker MCP 容器的网关服务",
        version="0.1.0",
        lifespan=lifespan,
    )
    
    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # 注册路由
    _register_routes(app)
    
    # 静态文件
    _setup_static_files(app)
    
    return app


def _setup_static_files(app: FastAPI) -> None:
    """设置静态文件"""
    # 查找 web 目录
    possible_paths = [
        Path(__file__).parent.parent.parent / "web",  # 开发环境
        Path(__file__).parent / "web",  # 打包环境
        Path("./web"),  # 当前目录
    ]
    
    web_dir = None
    for path in possible_paths:
        if path.exists():
            web_dir = path
            break
    
    if web_dir:
        logger.info("静态文件目录: %s", web_dir)
        app.mount("/static", StaticFiles(directory=web_dir), name="static")


def _register_routes(app: FastAPI) -> None:
    """注册路由"""
    
    # ==================== 根路由 ====================
    
    @app.get("/", include_in_schema=False)
    async def root():
        """重定向到 Dashboard"""
        return RedirectResponse(url="/static/index.html")
    
    # ==================== API 路由 ====================
    
    @app.get("/api/health")
    async def health_check():
        """健康检查端点"""
        return {"status": "ok", "service": "docker-mcp-gateway"}
    
    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        """获取网关状态"""
        if not _docker_manager or not _gateway_status:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        containers = _docker_manager.get_all_containers()
        running_count = sum(1 for c in containers if c.status == "running")
        total_requests = sum(c.stats.total_requests for c in containers)
        
        uptime = (datetime.now() - _gateway_status.start_time).total_seconds()
        
        return StatusResponse(
            start_time=_gateway_status.start_time.isoformat(),
            uptime_seconds=uptime,
            total_containers=len(containers),
            running_containers=running_count,
            total_requests=total_requests,
            memory_usage_mb=0.0,  # TODO: 计算总内存
        )
    
    @app.get("/api/containers")
    async def list_containers() -> list[ContainerResponse]:
        """列出所有容器"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        containers = _docker_manager.get_all_containers()
        result = []
        
        for info in containers:
            # 获取资源统计
            stats = await _docker_manager.get_container_stats(info.name)
            
            result.append(ContainerResponse(
                name=info.name,
                status=info.status,
                image=info.config.image,
                internal_port=info.config.internal_port,
                host_port=info.host_port or info.config.host_port,
                external_path=info.external_path or f"/mcp/{info.name}",
                internal_url=info.internal_url,
                health_status=info.health_status,
                total_requests=info.stats.total_requests,
                memory_mb=stats.get('memory_mb', 0.0),
                cpu_percent=stats.get('cpu_percent', 0.0),
                error_message=info.error_message,
            ))
        
        return result
    
    @app.post("/api/containers", status_code=201)
    async def create_container(req: CreateContainerRequest) -> ContainerResponse:
        """创建容器（解析 docker run 命令）"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        try:
            # 解析 docker run 命令
            config = _docker_manager.parse_docker_command(req.docker_command)
            
            # 检查是否重名
            existing_containers = _docker_manager.get_all_containers()
            if any(c.name == config.name for c in existing_containers):
                raise ValueError(f"容器名称 '{config.name}' 已存在，请使用不同的名称")
            
            # 创建容器（自动检测端口冲突并分配）
            info = await _docker_manager.create_container(config, import_existing=False)
            
            return ContainerResponse(
                name=info.name,
                status=info.status,
                image=info.config.image,
                internal_port=info.config.internal_port,
                host_port=info.host_port or info.config.host_port,
                external_path=info.external_path or f"/mcp/{info.name}",
                internal_url=info.internal_url,
                health_status=info.health_status,
                total_requests=0,
            )
            
        except ValueError as e:
            # 配置错误（如重名、格式错误）
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            # 运行时错误（如镜像拉取失败、端口分配失败）
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/containers/{name}")
    async def delete_container(name: str):
        """删除容器"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        logger.info("收到删除容器请求: %s", name)
        
        # 检查容器是否存在于管理列表中
        container_info = _docker_manager.get_container_info(name)
        if not container_info:
            logger.warning("容器 '%s' 不在管理列表中", name)
            raise HTTPException(status_code=404, detail=f"容器 '{name}' 不存在")
        
        success = await _docker_manager.remove_container(name)
        if not success:
            logger.error("删除容器 '%s' 失败", name)
            raise HTTPException(status_code=500, detail=f"删除容器 '{name}' 失败")
        
        logger.info("容器 '%s' 删除成功", name)
        return {"success": True, "message": f"容器 '{name}' 已删除"}
    
    @app.post("/api/containers/{name}/start")
    async def start_container(name: str):
        """启动容器"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        success = await _docker_manager.start_container(name)
        if not success:
            raise HTTPException(status_code=500, detail="启动失败")
        
        return {"success": True, "message": f"容器 '{name}' 已启动"}
    
    @app.post("/api/containers/{name}/stop")
    async def stop_container(name: str):
        """停止容器"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        success = await _docker_manager.stop_container(name)
        if not success:
            raise HTTPException(status_code=500, detail="停止失败")
        
        return {"success": True, "message": f"容器 '{name}' 已停止"}
    
    @app.post("/api/containers/{name}/restart")
    async def restart_container(name: str):
        """重启容器"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        success = await _docker_manager.restart_container(name)
        if not success:
            raise HTTPException(status_code=500, detail="重启失败")
        
        return {"success": True, "message": f"容器 '{name}' 已重启"}
    
    @app.get("/api/containers/{name}/logs")
    async def get_container_logs(name: str, tail: int = 100):
        """获取容器日志"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        logs = await _docker_manager.get_container_logs(name, tail=tail)
        return {"logs": logs}
    
    # ==================== MCP 代理路由 ====================
    
    @app.api_route(
        "/mcp/{server_name}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def proxy_mcp_request(server_name: str, path: str, request: Request):
        """代理 MCP 请求到对应容器"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        # 获取容器内部 URL
        internal_url = _docker_manager.get_container_internal_url(server_name)
        if not internal_url:
            raise HTTPException(
                status_code=404,
                detail=f"容器 '{server_name}' 不存在"
            )
        
        # 记录请求
        _docker_manager.record_request(server_name)
        
        # 构建目标 URL
        # 如果 path 为空（带尾斜杠的情况），转发到 /mcp
        if not path or path == "":
            target_url = f"{internal_url}/mcp"
        else:
            target_url = f"{internal_url}/{path}"
        
        # 代理请求
        proxy_client = get_proxy_client()
        return await proxy_client.proxy_request(request, target_url)
    
    # 处理 /mcp/{server_name} 没有尾随路径的情况
    @app.api_route(
        "/mcp/{server_name}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    )
    async def proxy_mcp_root(server_name: str, request: Request):
        """代理 MCP 根请求"""
        if not _docker_manager:
            raise HTTPException(status_code=503, detail="服务未就绪")
        
        internal_url = _docker_manager.get_container_internal_url(server_name)
        if not internal_url:
            raise HTTPException(
                status_code=404,
                detail=f"容器 '{server_name}' 不存在"
            )
        
        _docker_manager.record_request(server_name)
        
        # MCP 端点通常是 /mcp
        target_url = f"{internal_url}/mcp"
        
        proxy_client = get_proxy_client()
        return await proxy_client.proxy_request(request, target_url)
    
    # WebSocket 代理
    @app.websocket("/mcp/{server_name}/ws")
    async def proxy_mcp_websocket(websocket: WebSocket, server_name: str):
        """代理 MCP WebSocket 连接"""
        if not _docker_manager:
            await websocket.close(code=1011, reason="服务未就绪")
            return
        
        internal_url = _docker_manager.get_container_internal_url(server_name)
        if not internal_url:
            await websocket.close(code=1008, reason=f"容器 '{server_name}' 不存在")
            return
        
        _docker_manager.record_request(server_name)
        
        # 转换为 WebSocket URL
        ws_url = internal_url.replace("http://", "ws://") + "/ws"
        
        ws_proxy = get_websocket_proxy()
        await ws_proxy.proxy_websocket(websocket, ws_url)


# 创建应用实例
app = create_app()
