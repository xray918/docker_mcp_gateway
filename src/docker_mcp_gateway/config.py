"""配置管理模块"""

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .models import ContainerConfig, ContainerStats

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_CONFIG_DIR = "./config"
DEFAULT_DATA_DIR = "./data"


class ConfigManager:
    """配置管理器"""
    
    def __init__(
        self,
        config_dir: str | None = None,
        data_dir: str | None = None,
    ):
        self.config_dir = Path(config_dir or os.getenv("CONFIG_DIR", DEFAULT_CONFIG_DIR))
        self.data_dir = Path(data_dir or os.getenv("DATA_DIR", DEFAULT_DATA_DIR))
        
        # 确保目录存在
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 容器配置文件
        self.containers_file = self.config_dir / "containers.yaml"
        # 统计数据文件
        self.stats_file = self.data_dir / "stats.json"
        
        # 缓存
        self._containers: dict[str, ContainerConfig] = {}
        self._stats: dict[str, ContainerStats] = {}
        
        # 加载配置
        self._load_containers()
        self._load_stats()
    
    def _load_containers(self) -> None:
        """从文件加载容器配置"""
        if not self.containers_file.exists():
            logger.info("容器配置文件不存在，创建空配置: %s", self.containers_file)
            self._save_containers()
            return
        
        try:
            with open(self.containers_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            
            containers = data.get('containers', {})
            for name, config in containers.items():
                self._containers[name] = ContainerConfig(
                    name=name,
                    image=config.get('image', ''),
                    internal_port=config.get('internal_port', 8081),
                    host_port=config.get('host_port'),
                    env=config.get('env', {}),
                    restart_policy=config.get('restart_policy', 'always'),
                    labels=config.get('labels', {}),
                    memory_limit=config.get('memory_limit'),
                    cpu_limit=config.get('cpu_limit'),
                    raw_command=config.get('raw_command'),
                )
            
            logger.info("已加载 %d 个容器配置", len(self._containers))
        except Exception as e:
            logger.error("加载容器配置失败: %s", e)
    
    def _save_containers(self) -> None:
        """保存容器配置到文件"""
        try:
            containers = {}
            for name, config in self._containers.items():
                containers[name] = {
                    'image': config.image,
                    'internal_port': config.internal_port,
                    'host_port': config.host_port,
                    'env': config.env,
                    'restart_policy': config.restart_policy,
                    'labels': config.labels,
                    'memory_limit': config.memory_limit,
                    'cpu_limit': config.cpu_limit,
                    'raw_command': config.raw_command,
                }
            
            data = {'containers': containers}
            
            # 原子写入
            temp_file = self.containers_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            temp_file.replace(self.containers_file)
            
            logger.debug("容器配置已保存: %d 个", len(containers))
        except Exception as e:
            logger.error("保存容器配置失败: %s", e)
    
    def _load_stats(self) -> None:
        """从文件加载统计数据"""
        if not self.stats_file.exists():
            return
        
        try:
            with open(self.stats_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for name, stats in data.items():
                self._stats[name] = ContainerStats(
                    name=name,
                    total_requests=stats.get('total_requests', 0),
                )
            
            logger.info("已加载 %d 个容器统计数据", len(self._stats))
        except Exception as e:
            logger.error("加载统计数据失败: %s", e)
    
    def _save_stats(self) -> None:
        """保存统计数据到文件"""
        try:
            data = {}
            for name, stats in self._stats.items():
                data[name] = {
                    'total_requests': stats.total_requests,
                }
            
            # 原子写入
            temp_file = self.stats_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            temp_file.replace(self.stats_file)
        except Exception as e:
            logger.error("保存统计数据失败: %s", e)
    
    def get_all_containers(self) -> dict[str, ContainerConfig]:
        """获取所有容器配置"""
        return self._containers.copy()
    
    def get_container(self, name: str) -> ContainerConfig | None:
        """获取单个容器配置"""
        return self._containers.get(name)
    
    def add_container(self, config: ContainerConfig) -> None:
        """添加容器配置"""
        self._containers[config.name] = config
        if config.name not in self._stats:
            self._stats[config.name] = ContainerStats(name=config.name)
        self._save_containers()
        logger.info("已添加容器配置: %s", config.name)
    
    def remove_container(self, name: str) -> bool:
        """删除容器配置"""
        if name in self._containers:
            del self._containers[name]
            if name in self._stats:
                del self._stats[name]
            self._save_containers()
            self._save_stats()
            logger.info("已删除容器配置: %s", name)
            return True
        return False
    
    def get_stats(self, name: str) -> ContainerStats:
        """获取容器统计数据"""
        if name not in self._stats:
            self._stats[name] = ContainerStats(name=name)
        return self._stats[name]
    
    def increment_requests(self, name: str) -> None:
        """增加请求计数"""
        from datetime import datetime
        stats = self.get_stats(name)
        stats.total_requests += 1
        stats.last_access_time = datetime.now()
        # 定期保存（每100次请求）
        if stats.total_requests % 100 == 0:
            self._save_stats()
    
    def save_all(self) -> None:
        """保存所有数据"""
        self._save_containers()
        self._save_stats()
