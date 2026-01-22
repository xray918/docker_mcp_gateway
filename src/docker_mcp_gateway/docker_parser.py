"""Docker run 命令解析器

解析 docker run 命令字符串，提取容器配置信息。
"""

import re
import shlex
from dataclasses import dataclass, field


@dataclass
class ParsedDockerRun:
    """解析后的 docker run 命令"""
    image: str = ""
    name: str | None = None
    ports: list[tuple[int, int]] = field(default_factory=list)  # (host_port, container_port)
    env: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    restart_policy: str | None = None
    detach: bool = False
    interactive: bool = False
    tty: bool = False
    network: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    memory: str | None = None
    cpus: str | None = None
    extra_args: list[str] = field(default_factory=list)
    raw_command: str = ""


def parse_docker_run(command: str) -> ParsedDockerRun:
    """解析 docker run 命令字符串
    
    支持的参数：
    - --name: 容器名称
    - -p/--publish: 端口映射
    - -e/--env: 环境变量
    - -v/--volume: 卷挂载
    - --restart: 重启策略
    - -d/--detach: 后台运行
    - -i/--interactive: 交互模式
    - -t/--tty: 分配终端
    - --network: 网络
    - -l/--label: 标签
    - --memory/-m: 内存限制
    - --cpus: CPU 限制
    
    Args:
        command: docker run 命令字符串
        
    Returns:
        ParsedDockerRun: 解析结果
        
    Raises:
        ValueError: 命令格式错误
    """
    result = ParsedDockerRun(raw_command=command.strip())
    
    # 清理命令：移除换行符和多余空格
    cleaned = command.replace('\\\n', ' ').replace('\\', ' ')
    cleaned = ' '.join(cleaned.split())
    
    # 使用 shlex 分割参数（处理引号）
    try:
        tokens = shlex.split(cleaned)
    except ValueError as e:
        raise ValueError(f"命令解析错误: {e}")
    
    if not tokens:
        raise ValueError("空命令")
    
    # 跳过 "docker" 和 "run"
    i = 0
    while i < len(tokens) and tokens[i] in ("docker", "run"):
        i += 1
    
    # 解析参数
    while i < len(tokens):
        token = tokens[i]
        
        # 检查是否是镜像名（不以 - 开头，且不是参数值）
        if not token.startswith('-'):
            # 这应该是镜像名
            result.image = token
            i += 1
            # 剩余的是容器命令参数
            result.extra_args = tokens[i:]
            break
        
        # -d, --detach
        if token in ('-d', '--detach'):
            result.detach = True
            i += 1
            continue
        
        # -i, --interactive
        if token in ('-i', '--interactive'):
            result.interactive = True
            i += 1
            continue
        
        # -t, --tty
        if token in ('-t', '--tty'):
            result.tty = True
            i += 1
            continue
        
        # -dit 组合
        if token.startswith('-') and not token.startswith('--') and len(token) > 2:
            flags = token[1:]
            for f in flags:
                if f == 'd':
                    result.detach = True
                elif f == 'i':
                    result.interactive = True
                elif f == 't':
                    result.tty = True
            i += 1
            continue
        
        # --name
        if token == '--name':
            if i + 1 < len(tokens):
                result.name = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('--name='):
            result.name = token.split('=', 1)[1]
            i += 1
            continue
        
        # -p, --publish (端口映射)
        if token in ('-p', '--publish'):
            if i + 1 < len(tokens):
                port_mapping = tokens[i + 1]
                parsed_port = _parse_port_mapping(port_mapping)
                if parsed_port:
                    result.ports.append(parsed_port)
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('-p=') or token.startswith('--publish='):
            port_mapping = token.split('=', 1)[1]
            parsed_port = _parse_port_mapping(port_mapping)
            if parsed_port:
                result.ports.append(parsed_port)
            i += 1
            continue
        
        # -e, --env (环境变量)
        if token in ('-e', '--env'):
            if i + 1 < len(tokens):
                env_str = tokens[i + 1]
                key, value = _parse_env(env_str)
                if key:
                    result.env[key] = value
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('-e=') or token.startswith('--env='):
            env_str = token.split('=', 1)[1]
            key, value = _parse_env(env_str)
            if key:
                result.env[key] = value
            i += 1
            continue
        
        # -v, --volume (卷挂载)
        if token in ('-v', '--volume'):
            if i + 1 < len(tokens):
                result.volumes.append(tokens[i + 1])
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('-v=') or token.startswith('--volume='):
            result.volumes.append(token.split('=', 1)[1])
            i += 1
            continue
        
        # --restart
        if token == '--restart':
            if i + 1 < len(tokens):
                result.restart_policy = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('--restart='):
            result.restart_policy = token.split('=', 1)[1]
            i += 1
            continue
        
        # --network
        if token == '--network':
            if i + 1 < len(tokens):
                result.network = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('--network='):
            result.network = token.split('=', 1)[1]
            i += 1
            continue
        
        # -l, --label
        if token in ('-l', '--label'):
            if i + 1 < len(tokens):
                label_str = tokens[i + 1]
                key, value = _parse_env(label_str)  # 格式相同
                if key:
                    result.labels[key] = value
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('-l=') or token.startswith('--label='):
            label_str = token.split('=', 1)[1]
            key, value = _parse_env(label_str)
            if key:
                result.labels[key] = value
            i += 1
            continue
        
        # --memory, -m
        if token in ('-m', '--memory'):
            if i + 1 < len(tokens):
                result.memory = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('-m=') or token.startswith('--memory='):
            result.memory = token.split('=', 1)[1]
            i += 1
            continue
        
        # --cpus
        if token == '--cpus':
            if i + 1 < len(tokens):
                result.cpus = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue
        
        if token.startswith('--cpus='):
            result.cpus = token.split('=', 1)[1]
            i += 1
            continue
        
        # 跳过未知参数
        i += 1
    
    # 验证必需字段
    if not result.image:
        raise ValueError("未找到 Docker 镜像名称")
    
    return result


def _parse_port_mapping(mapping: str) -> tuple[int, int] | None:
    """解析端口映射
    
    支持格式：
    - 8080:80
    - 0.0.0.0:8080:80
    - 8080:80/tcp
    
    Returns:
        (host_port, container_port) 或 None
    """
    # 移除协议后缀
    mapping = re.sub(r'/(tcp|udp)$', '', mapping)
    
    parts = mapping.split(':')
    try:
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
        elif len(parts) == 3:
            # 0.0.0.0:8080:80
            return (int(parts[1]), int(parts[2]))
    except ValueError:
        pass
    return None


def _parse_env(env_str: str) -> tuple[str, str]:
    """解析环境变量
    
    格式：KEY=VALUE
    
    Returns:
        (key, value)
    """
    if '=' in env_str:
        key, value = env_str.split('=', 1)
        return (key.strip(), value.strip())
    return (env_str.strip(), "")
