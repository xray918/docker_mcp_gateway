"""Docker 容器管理模块"""

import asyncio
import logging
import socket
from datetime import datetime
from typing import Any

import docker
from docker.errors import APIError, ContainerError, ImageNotFound, NotFound
from docker.models.containers import Container

from .config import ConfigManager
from .docker_parser import ParsedDockerRun, parse_docker_run
from .models import ContainerConfig, ContainerInfo, ContainerStats

logger = logging.getLogger(__name__)

# 网关管理的容器标签
GATEWAY_LABEL = "docker-mcp-gateway.managed"
GATEWAY_NAME_LABEL = "docker-mcp-gateway.name"

# 自动端口分配范围
AUTO_PORT_START = 18100
AUTO_PORT_END = 18999


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """检查端口是否可用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_available_port(start: int = AUTO_PORT_START, end: int = AUTO_PORT_END) -> int | None:
    """查找可用端口
    
    Args:
        start: 起始端口
        end: 结束端口
        
    Returns:
        可用端口号，如果没有找到返回 None
    """
    for port in range(start, end + 1):
        if is_port_available(port):
            return port
    return None


class DockerManager:
    """Docker 容器管理器"""
    
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self._client: docker.DockerClient | None = None
        self._network_name = "mcp-gateway-network"
        
        # 容器信息缓存
        self._container_info: dict[str, ContainerInfo] = {}
        
        # 健康检查任务
        self._health_check_task: asyncio.Task | None = None
        self._health_check_interval = 30  # 秒
    
    @property
    def client(self) -> docker.DockerClient:
        """获取 Docker 客户端（延迟初始化）"""
        if self._client is None:
            self._client = docker.from_env()
        return self._client
    
    async def initialize(self) -> None:
        """初始化 Docker 管理器"""
        logger.info("初始化 Docker 管理器...")
        
        # 测试 Docker 连接
        try:
            info = await asyncio.to_thread(self.client.ping)
            logger.info("Docker 连接成功")
        except Exception as e:
            logger.error("Docker 连接失败: %s", e)
            raise RuntimeError(f"无法连接到 Docker: {e}")
        
        # 确保网络存在
        await self._ensure_network()
        
        # 同步现有容器状态
        await self._sync_containers()
        
        # 启动健康检查
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("Docker 管理器初始化完成")
    
    async def _ensure_network(self) -> None:
        """确保 Gateway 网络存在"""
        try:
            networks = await asyncio.to_thread(
                self.client.networks.list,
                names=[self._network_name]
            )
            if not networks:
                await asyncio.to_thread(
                    self.client.networks.create,
                    self._network_name,
                    driver="bridge"
                )
                logger.info("已创建 Docker 网络: %s", self._network_name)
            else:
                logger.debug("Docker 网络已存在: %s", self._network_name)
        except Exception as e:
            logger.warning("创建/检查 Docker 网络失败: %s", e)
    
    async def _sync_containers(self) -> None:
        """同步配置文件中的容器状态"""
        configs = self.config.get_all_containers()
        
        for name, config in configs.items():
            try:
                container = await self._get_container(name)
                host_port = config.host_port
                
                if container:
                    status = container.status
                    container_id = container.id
                    # 从容器获取实际的主机端口
                    actual_port = self._get_host_port_from_container(container, config.internal_port)
                    if actual_port:
                        host_port = actual_port
                        # 更新配置中的端口
                        if config.host_port != host_port:
                            config.host_port = host_port
                            self.config.add_container(config)
                else:
                    status = "not_created"
                    container_id = None
                
                # 计算内部 URL - 使用主机端口访问
                if host_port:
                    internal_url = f"http://localhost:{host_port}"
                else:
                    # 回退到容器名称（Docker 网络模式）
                    internal_url = f"http://{name}:{config.internal_port}"
                
                self._container_info[name] = ContainerInfo(
                    name=name,
                    config=config,
                    status=status,
                    container_id=container_id,
                    internal_url=internal_url,
                    external_path=f"/mcp/{name}",
                    stats=self.config.get_stats(name),
                    host_port=host_port,
                )
                
                logger.debug("同步容器状态: %s -> %s (端口: %s)", name, status, host_port)
            except Exception as e:
                logger.error("同步容器 %s 状态失败: %s", name, e)
    
    def _get_host_port_from_container(self, container: Container, internal_port: int) -> int | None:
        """从容器获取主机端口"""
        try:
            ports = container.attrs.get('NetworkSettings', {}).get('Ports', {})
            port_key = f"{internal_port}/tcp"
            bindings = ports.get(port_key, [])
            if bindings and bindings[0]:
                return int(bindings[0].get('HostPort', 0))
        except Exception as e:
            logger.debug("获取容器端口失败: %s", e)
        return None
    
    async def _get_container(self, name: str) -> Container | None:
        """获取容器对象"""
        try:
            container = await asyncio.to_thread(
                self.client.containers.get,
                name
            )
            return container
        except NotFound:
            return None
        except Exception as e:
            logger.error("获取容器 %s 失败: %s", name, e)
            return None
    
    async def _health_check_loop(self) -> None:
        """健康检查循环"""
        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
                await self._check_all_containers_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("健康检查异常: %s", e)
    
    async def _check_all_containers_health(self) -> None:
        """检查所有容器健康状态"""
        for name in list(self._container_info.keys()):
            try:
                container = await self._get_container(name)
                if container:
                    info = self._container_info.get(name)
                    if info:
                        info.status = container.status
                        # 检查健康状态
                        health = container.attrs.get('State', {}).get('Health', {})
                        info.health_status = health.get('Status', 'unknown')
            except Exception as e:
                logger.debug("检查容器 %s 健康状态失败: %s", name, e)
    
    async def _import_existing_container(
        self,
        name: str,
        config: ContainerConfig,
        container: Container,
    ) -> ContainerInfo:
        """导入已存在的容器到 Gateway 管理
        
        Args:
            name: 容器名称
            config: 容器配置
            container: Docker 容器对象
            
        Returns:
            ContainerInfo: 容器信息
        """
        # 将容器连接到 Gateway 网络（如果未连接）
        try:
            networks = container.attrs.get('NetworkSettings', {}).get('Networks', {})
            if self._network_name not in networks:
                network = await asyncio.to_thread(
                    self.client.networks.get,
                    self._network_name
                )
                await asyncio.to_thread(network.connect, container)
                logger.info("已将容器 '%s' 连接到 Gateway 网络", name)
        except Exception as e:
            logger.warning("连接容器到 Gateway 网络失败: %s", e)
        
        # 获取实际的主机端口
        host_port = self._get_host_port_from_container(container, config.internal_port)
        if host_port:
            config.host_port = host_port
        
        # 保存配置
        self.config.add_container(config)
        
        # 更新缓存 - 使用主机端口访问
        if config.host_port:
            internal_url = f"http://localhost:{config.host_port}"
        else:
            internal_url = f"http://{name}:{config.internal_port}"
        
        info = ContainerInfo(
            name=name,
            config=config,
            status=container.status,
            container_id=container.id,
            internal_url=internal_url,
            external_path=f"/mcp/{name}",
            stats=self.config.get_stats(name),
            host_port=config.host_port,
        )
        self._container_info[name] = info
        
        logger.info("容器 '%s' 已导入 Gateway 管理 (端口: %s)", name, config.host_port)
        return info
    
    def _get_used_ports(self) -> set[int]:
        """获取已使用的主机端口"""
        used_ports = set()
        
        # 从配置中获取
        for config in self.config.get_all_containers().values():
            if config.host_port:
                used_ports.add(config.host_port)
        
        # 从运行中的容器获取
        try:
            containers = self.client.containers.list(all=True)
            for container in containers:
                ports = container.attrs.get('NetworkSettings', {}).get('Ports', {})
                for port_bindings in ports.values():
                    if port_bindings:
                        for binding in port_bindings:
                            if binding.get('HostPort'):
                                used_ports.add(int(binding['HostPort']))
        except Exception as e:
            logger.debug("获取容器端口列表失败: %s", e)
        
        return used_ports
    
    def _check_name_conflict(self, name: str) -> tuple[bool, str | None]:
        """检查容器名称是否冲突
        
        Returns:
            (is_conflict, error_message)
        """
        # 检查配置中是否已存在
        if name in self.config.get_all_containers():
            return True, f"容器名称 '{name}' 已存在于配置中"
        
        # 检查 Docker 中是否已存在
        try:
            container = self.client.containers.get(name)
            # 容器存在，检查是否由本 Gateway 管理
            labels = container.labels or {}
            if labels.get(GATEWAY_LABEL) == "true":
                return False, None  # 由本 Gateway 管理，可以导入
            else:
                return True, f"容器名称 '{name}' 已被其他服务使用"
        except NotFound:
            return False, None
        except Exception as e:
            logger.warning("检查容器名称时出错: %s", e)
            return False, None
    
    def _check_port_conflict(self, host_port: int) -> tuple[bool, str | None]:
        """检查主机端口是否冲突
        
        Returns:
            (is_conflict, error_message)
        """
        if not host_port:
            return False, None
        
        # 检查端口是否被系统占用
        if not is_port_available(host_port):
            return True, f"主机端口 {host_port} 已被占用"
        
        # 检查端口是否已被其他容器配置
        used_ports = self._get_used_ports()
        if host_port in used_ports:
            return True, f"主机端口 {host_port} 已被其他容器使用"
        
        return False, None
    
    def _allocate_port(self, preferred_port: int | None = None) -> int:
        """分配可用的主机端口
        
        Args:
            preferred_port: 首选端口
            
        Returns:
            可用的端口号
            
        Raises:
            RuntimeError: 无法分配端口
        """
        # 如果指定了首选端口且可用，使用它
        if preferred_port:
            conflict, _ = self._check_port_conflict(preferred_port)
            if not conflict:
                return preferred_port
            logger.warning("首选端口 %d 不可用，将自动分配", preferred_port)
        
        # 获取已使用的端口
        used_ports = self._get_used_ports()
        
        # 查找可用端口
        for port in range(AUTO_PORT_START, AUTO_PORT_END + 1):
            if port not in used_ports and is_port_available(port):
                logger.info("自动分配端口: %d", port)
                return port
        
        raise RuntimeError(f"无法分配端口，范围 {AUTO_PORT_START}-{AUTO_PORT_END} 内没有可用端口")
    
    def parse_docker_command(self, command: str) -> ContainerConfig:
        """解析 docker run 命令并返回容器配置
        
        Args:
            command: docker run 命令字符串
            
        Returns:
            ContainerConfig: 容器配置
            
        Raises:
            ValueError: 命令格式错误
        """
        parsed = parse_docker_run(command)
        
        # 提取容器名称
        name = parsed.name
        if not name:
            # 从镜像名生成
            image_name = parsed.image.split('/')[-1].split(':')[0]
            name = image_name.replace('.', '-').replace('_', '-')
        
        # 提取端口映射
        internal_port = 8081  # 默认
        host_port = None
        if parsed.ports:
            # 使用第一个端口映射
            host_port = parsed.ports[0][0]  # 主机端口
            internal_port = parsed.ports[0][1]  # 容器端口
        
        # 资源限制
        memory_limit = parsed.memory
        cpu_limit = None
        if parsed.cpus:
            try:
                cpu_limit = float(parsed.cpus)
            except ValueError:
                pass
        
        return ContainerConfig(
            name=name,
            image=parsed.image,
            internal_port=internal_port,
            host_port=host_port,
            env=parsed.env,
            restart_policy=parsed.restart_policy or "always",
            labels=parsed.labels,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            raw_command=command,
        )
    
    async def create_container(self, config: ContainerConfig, import_existing: bool = True) -> ContainerInfo:
        """创建并启动容器
        
        自动执行以下检测：
        1. 重名检测 - 检查容器名称是否冲突
        2. 端口冲突检测 - 检查主机端口是否可用
        3. 自动端口分配 - 如果端口冲突则自动分配新端口
        
        Args:
            config: 容器配置
            import_existing: 如果容器已存在，是否导入管理（默认 True）
            
        Returns:
            ContainerInfo: 容器信息
            
        Raises:
            ValueError: 配置错误或冲突
            RuntimeError: 创建失败
        """
        name = config.name
        
        # ===== 1. 重名检测 =====
        is_conflict, error_msg = self._check_name_conflict(name)
        if is_conflict and not import_existing:
            raise ValueError(error_msg)
        
        # 检查 Docker 中是否已存在
        existing = await self._get_container(name)
        if existing:
            if import_existing:
                # 导入已存在的容器
                logger.info("容器 '%s' 已存在，导入到 Gateway 管理", name)
                return await self._import_existing_container(name, config, existing)
            else:
                raise ValueError(f"容器 '{name}' 已存在")
        
        # 如果名称已在配置中（但 Docker 中不存在），说明是残留配置
        if name in self.config.get_all_containers():
            logger.warning("发现残留配置 '%s'，将覆盖", name)
        
        # ===== 2. 端口冲突检测与自动分配 =====
        original_port = config.host_port
        if config.host_port:
            is_conflict, error_msg = self._check_port_conflict(config.host_port)
            if is_conflict:
                logger.warning("端口冲突: %s", error_msg)
                # 自动分配新端口
                config.host_port = self._allocate_port(config.host_port)
                logger.info("已自动分配端口: %d -> %d", original_port or 0, config.host_port)
        else:
            # 没有指定端口，自动分配
            config.host_port = self._allocate_port()
            logger.info("已自动分配端口: %d", config.host_port)
        
        logger.info("正在创建容器: %s (镜像: %s, 端口: %d:%d)", 
                    name, config.image, config.host_port, config.internal_port)
        
        try:
            # 拉取镜像
            logger.info("拉取镜像: %s", config.image)
            await asyncio.to_thread(self.client.images.pull, config.image)
            
            # 准备容器参数
            container_kwargs: dict[str, Any] = {
                'image': config.image,
                'name': name,
                'detach': True,
                'environment': config.env,
                'labels': {
                    GATEWAY_LABEL: "true",
                    GATEWAY_NAME_LABEL: name,
                    **config.labels,
                },
            }
            
            # 端口映射（必须）- Gateway 需要通过主机端口访问容器
            if config.host_port:
                container_kwargs['ports'] = {
                    f'{config.internal_port}/tcp': config.host_port
                }
            else:
                # 如果没有指定主机端口，自动分配
                container_kwargs['ports'] = {
                    f'{config.internal_port}/tcp': None  # Docker 自动分配端口
                }
            
            # 重启策略
            if config.restart_policy:
                container_kwargs['restart_policy'] = {
                    'Name': config.restart_policy
                }
            
            # 资源限制
            if config.memory_limit:
                container_kwargs['mem_limit'] = config.memory_limit
            if config.cpu_limit:
                container_kwargs['nano_cpus'] = int(config.cpu_limit * 1e9)
            
            # 创建容器
            container = await asyncio.to_thread(
                self.client.containers.run,
                **container_kwargs
            )
            
            logger.info("容器创建成功: %s (ID: %s)", name, container.short_id)
            
            # 获取实际分配的端口（Docker 自动分配时）
            container.reload()  # 刷新容器信息
            actual_port = self._get_host_port_from_container(container, config.internal_port)
            if actual_port and actual_port != config.host_port:
                logger.info("Docker 分配端口: %d -> %d", config.host_port or 0, actual_port)
                config.host_port = actual_port
            
            # 保存配置
            self.config.add_container(config)
            
            # 更新缓存 - 使用主机端口访问
            internal_url = f"http://localhost:{config.host_port}"
            info = ContainerInfo(
                name=name,
                config=config,
                status="running",
                container_id=container.id,
                internal_url=internal_url,
                external_path=f"/mcp/{name}",
                stats=self.config.get_stats(name),
                host_port=config.host_port,
            )
            info.stats.created_at = datetime.now()
            info.stats.started_at = datetime.now()
            self._container_info[name] = info
            
            logger.info("容器已上线: %s, 访问地址: /mcp/%s (内部: %s)", name, name, internal_url)
            
            return info
            
        except ImageNotFound as e:
            raise RuntimeError(f"镜像不存在: {config.image}")
        except APIError as e:
            raise RuntimeError(f"Docker API 错误: {e}")
        except Exception as e:
            logger.exception("创建容器失败")
            raise RuntimeError(f"创建容器失败: {e}")
    
    async def remove_container(self, name: str, force: bool = True) -> bool:
        """删除容器
        
        Args:
            name: 容器名称
            force: 是否强制删除（停止运行中的容器）
            
        Returns:
            bool: 是否成功
        """
        logger.info("正在删除容器: %s", name)
        
        try:
            container = await self._get_container(name)
            if container:
                await asyncio.to_thread(container.remove, force=force)
                logger.info("容器已删除: %s", name)
            
            # 删除配置
            self.config.remove_container(name)
            
            # 删除缓存
            if name in self._container_info:
                del self._container_info[name]
            
            return True
            
        except Exception as e:
            logger.error("删除容器 %s 失败: %s", name, e)
            return False
    
    async def start_container(self, name: str) -> bool:
        """启动容器"""
        logger.info("正在启动容器: %s", name)
        
        try:
            container = await self._get_container(name)
            if not container:
                # 容器不存在，尝试创建
                config = self.config.get_container(name)
                if config:
                    await self.create_container(config)
                    return True
                else:
                    logger.error("容器配置不存在: %s", name)
                    return False
            
            if container.status != "running":
                await asyncio.to_thread(container.start)
                logger.info("容器已启动: %s", name)
            
            # 更新状态
            if name in self._container_info:
                self._container_info[name].status = "running"
                self._container_info[name].stats.started_at = datetime.now()
            
            return True
            
        except Exception as e:
            logger.error("启动容器 %s 失败: %s", name, e)
            return False
    
    async def stop_container(self, name: str, timeout: int = 10) -> bool:
        """停止容器"""
        logger.info("正在停止容器: %s", name)
        
        try:
            container = await self._get_container(name)
            if container and container.status == "running":
                await asyncio.to_thread(container.stop, timeout=timeout)
                logger.info("容器已停止: %s", name)
            
            # 更新状态
            if name in self._container_info:
                self._container_info[name].status = "exited"
            
            return True
            
        except Exception as e:
            logger.error("停止容器 %s 失败: %s", name, e)
            return False
    
    async def restart_container(self, name: str, timeout: int = 10) -> bool:
        """重启容器"""
        logger.info("正在重启容器: %s", name)
        
        try:
            container = await self._get_container(name)
            if container:
                await asyncio.to_thread(container.restart, timeout=timeout)
                logger.info("容器已重启: %s", name)
                
                # 更新状态
                if name in self._container_info:
                    self._container_info[name].status = "running"
                    self._container_info[name].stats.started_at = datetime.now()
                
                return True
            return False
            
        except Exception as e:
            logger.error("重启容器 %s 失败: %s", name, e)
            return False
    
    async def get_container_logs(
        self,
        name: str,
        tail: int = 100,
        since: int | None = None,
    ) -> str:
        """获取容器日志
        
        Args:
            name: 容器名称
            tail: 返回最后多少行
            since: 只返回此时间戳之后的日志（Unix 时间戳）
            
        Returns:
            str: 日志内容
        """
        try:
            container = await self._get_container(name)
            if not container:
                return f"容器 '{name}' 不存在"
            
            kwargs: dict[str, Any] = {
                'tail': tail,
                'timestamps': True,
            }
            if since:
                kwargs['since'] = since
            
            logs = await asyncio.to_thread(
                container.logs,
                **kwargs
            )
            
            if isinstance(logs, bytes):
                return logs.decode('utf-8', errors='replace')
            return str(logs)
            
        except Exception as e:
            logger.error("获取容器 %s 日志失败: %s", name, e)
            return f"获取日志失败: {e}"
    
    async def get_container_stats(self, name: str) -> dict[str, Any]:
        """获取容器资源使用统计"""
        try:
            container = await self._get_container(name)
            if not container:
                return {}
            
            stats = await asyncio.to_thread(
                container.stats,
                stream=False
            )
            
            # 解析内存使用
            memory_stats = stats.get('memory_stats', {})
            memory_usage = memory_stats.get('usage', 0)
            memory_limit = memory_stats.get('limit', 1)
            memory_mb = memory_usage / (1024 * 1024)
            memory_percent = (memory_usage / memory_limit) * 100 if memory_limit else 0
            
            # 解析 CPU 使用
            cpu_stats = stats.get('cpu_stats', {})
            precpu_stats = stats.get('precpu_stats', {})
            cpu_percent = 0.0
            
            cpu_delta = (
                cpu_stats.get('cpu_usage', {}).get('total_usage', 0) -
                precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
            )
            system_delta = (
                cpu_stats.get('system_cpu_usage', 0) -
                precpu_stats.get('system_cpu_usage', 0)
            )
            
            if system_delta > 0:
                cpu_count = cpu_stats.get('online_cpus', 1)
                cpu_percent = (cpu_delta / system_delta) * cpu_count * 100
            
            return {
                'memory_mb': round(memory_mb, 2),
                'memory_percent': round(memory_percent, 2),
                'cpu_percent': round(cpu_percent, 2),
            }
            
        except Exception as e:
            logger.debug("获取容器 %s 统计信息失败: %s", name, e)
            return {}
    
    def get_container_info(self, name: str) -> ContainerInfo | None:
        """获取容器信息"""
        return self._container_info.get(name)
    
    def get_all_containers(self) -> list[ContainerInfo]:
        """获取所有容器信息"""
        return list(self._container_info.values())
    
    def get_container_internal_url(self, name: str) -> str | None:
        """获取容器访问 URL
        
        优先使用端口映射（localhost:host_port），如果没有端口映射则使用容器 IP。
        """
        info = self._container_info.get(name)
        config = info.config if info else self.config.get_container(name)
        
        if not config:
            return None
        
        # 优先使用端口映射（Gateway 运行在主机上）
        try:
            container = self.client.containers.get(name)
            ports = container.attrs.get('NetworkSettings', {}).get('Ports', {})
            
            # 查找容器内部端口的映射
            port_key = f"{config.internal_port}/tcp"
            if port_key in ports and ports[port_key]:
                host_port = ports[port_key][0].get('HostPort')
                if host_port:
                    return f"http://127.0.0.1:{host_port}"
            
            # 如果没有端口映射，使用容器 IP
            networks = container.attrs.get('NetworkSettings', {}).get('Networks', {})
            # 优先使用 gateway 网络的 IP
            if self._network_name in networks:
                ip = networks[self._network_name].get('IPAddress')
                if ip:
                    return f"http://{ip}:{config.internal_port}"
            # 使用任意网络的 IP
            for network in networks.values():
                ip = network.get('IPAddress')
                if ip:
                    return f"http://{ip}:{config.internal_port}"
        except Exception as e:
            logger.debug("获取容器 %s URL 失败: %s", name, e)
        
        # 回退到配置中的内部 URL
        if info and info.internal_url:
            return info.internal_url
        
        return f"http://127.0.0.1:{config.internal_port}"
    
    def record_request(self, name: str) -> None:
        """记录请求"""
        self.config.increment_requests(name)
        if name in self._container_info:
            self._container_info[name].stats.total_requests += 1
            self._container_info[name].stats.last_access_time = datetime.now()
    
    async def cleanup(self) -> None:
        """清理资源"""
        logger.info("清理 Docker 管理器...")
        
        # 停止健康检查
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # 保存配置
        self.config.save_all()
        
        # 关闭 Docker 客户端
        if self._client:
            self._client.close()
        
        logger.info("Docker 管理器清理完成")
