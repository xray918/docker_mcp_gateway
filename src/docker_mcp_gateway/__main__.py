"""Docker MCP Gateway 入口模块"""

import logging
import os
import signal
import socket
import subprocess
import sys

import uvicorn


def setup_logging() -> None:
    """配置日志"""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # 降低第三方库日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("docker").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def find_process_on_port(port: int) -> list[tuple[int, str]]:
    """查找占用指定端口的进程
    
    Returns:
        list of (pid, process_name) tuples
    """
    processes = []
    try:
        # macOS/Linux: 使用 lsof
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    # 获取进程名
                    ps_result = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "comm="],
                        capture_output=True,
                        text=True,
                    )
                    name = ps_result.stdout.strip() if ps_result.returncode == 0 else "unknown"
                    processes.append((pid, name))
                except ValueError:
                    pass
    except FileNotFoundError:
        # lsof 不存在，尝试其他方法
        pass
    return processes


def kill_process_on_port(port: int, force: bool = False) -> bool:
    """清理占用端口的进程
    
    Args:
        port: 端口号
        force: 是否强制杀死进程
        
    Returns:
        bool: 是否成功清理
    """
    logger = logging.getLogger(__name__)
    
    processes = find_process_on_port(port)
    if not processes:
        return True
    
    for pid, name in processes:
        logger.warning("发现端口 %d 被进程占用: PID=%d (%s)", port, pid, name)
        
        try:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, sig)
            logger.info("已发送信号到进程 %d", pid)
        except ProcessLookupError:
            logger.debug("进程 %d 已不存在", pid)
        except PermissionError:
            logger.error("无权限杀死进程 %d，请使用 sudo 或手动清理", pid)
            return False
    
    # 等待端口释放
    import time
    for _ in range(10):
        time.sleep(0.5)
        if not is_port_in_use(port):
            logger.info("端口 %d 已释放", port)
            return True
    
    logger.error("端口 %d 仍被占用", port)
    return False


def main() -> None:
    """主入口函数"""
    setup_logging()
    
    logger = logging.getLogger(__name__)
    
    # 获取配置
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "18082"))  # 默认端口
    
    # 检查端口冲突
    if is_port_in_use(port, host):
        logger.warning("端口 %d 已被占用，尝试清理...", port)
        
        # 显示占用进程
        processes = find_process_on_port(port)
        for pid, name in processes:
            logger.info("  - PID %d: %s", pid, name)
        
        # 尝试清理
        if kill_process_on_port(port):
            logger.info("端口冲突已解决")
        else:
            logger.error("无法清理端口 %d，请手动处理", port)
            logger.info("可以运行: kill -9 $(lsof -t -i:%d)", port)
            sys.exit(1)
    
    logger.info("=" * 50)
    logger.info("Docker MCP Gateway 启动")
    logger.info("=" * 50)
    logger.info("监听地址: http://%s:%d", host, port)
    logger.info("Dashboard: http://%s:%d/", host, port)
    logger.info("API 文档: http://%s:%d/docs", host, port)
    logger.info("MCP 代理: http://%s:%d/mcp/{container_name}", host, port)
    logger.info("=" * 50)
    
    # 启动服务器
    try:
        uvicorn.run(
            "docker_mcp_gateway.app:app",
            host=host,
            port=port,
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
        )
    except OSError as e:
        if "address already in use" in str(e).lower():
            logger.error("端口 %d 被占用，尝试清理并重启...", port)
            if kill_process_on_port(port, force=True):
                # 重试启动
                uvicorn.run(
                    "docker_mcp_gateway.app:app",
                    host=host,
                    port=port,
                    log_level=os.getenv("LOG_LEVEL", "info").lower(),
                )
            else:
                logger.error("启动失败: %s", e)
                sys.exit(1)
        else:
            raise


if __name__ == "__main__":
    main()
