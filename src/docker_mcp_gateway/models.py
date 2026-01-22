"""数据模型定义"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ContainerConfig:
    """容器配置"""
    name: str                           # 容器名称（也是路由名称）
    image: str                          # Docker 镜像
    internal_port: int = 8081           # 容器内部端口
    host_port: int | None = None        # 主机端口（端口映射）
    env: dict[str, str] = field(default_factory=dict)  # 环境变量
    restart_policy: str = "always"      # 重启策略
    labels: dict[str, str] = field(default_factory=dict)  # 标签
    
    # 资源限制
    memory_limit: str | None = None     # 内存限制 (如 "512m")
    cpu_limit: float | None = None      # CPU 限制 (如 1.0)
    
    # 原始 docker run 命令（用于显示）
    raw_command: str | None = None


@dataclass
class ContainerStats:
    """容器统计信息"""
    name: str
    total_requests: int = 0
    last_access_time: datetime | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    
    # 运行时信息
    memory_usage_mb: float = 0.0
    cpu_percent: float = 0.0


@dataclass
class ContainerInfo:
    """容器完整信息"""
    name: str
    config: ContainerConfig
    status: str = "unknown"             # running, stopped, starting, error
    container_id: str | None = None
    internal_url: str | None = None     # 内部访问 URL (localhost:host_port)
    external_path: str | None = None    # 外部路由路径 (/mcp/{name})
    health_status: str = "unknown"      # healthy, unhealthy, unknown
    host_port: int | None = None        # 主机映射端口
    stats: ContainerStats = field(default_factory=lambda: ContainerStats(name=""))
    error_message: str | None = None
    
    def __post_init__(self):
        if self.stats.name != self.name:
            self.stats.name = self.name


@dataclass
class GatewayStatus:
    """网关状态"""
    start_time: datetime
    total_containers: int = 0
    running_containers: int = 0
    total_requests: int = 0
    memory_usage_mb: float = 0.0
    last_activity: datetime | None = None
