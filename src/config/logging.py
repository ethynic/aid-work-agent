"""
日志配置模块

使用loguru进行日志管理
"""

import sys
from pathlib import Path

from loguru import logger


def setup_logging(
    log_level: str = "INFO",
    log_dir: str = "log/agent",
    rotation: str = "10 MB",
    retention: str = "7 days",
    json_format: bool = False,
) -> None:
    """
    配置日志系统
    
    Args:
        log_level: 日志级别
        log_dir: 日志目录
        rotation: 日志轮转大小
        retention: 日志保留时间
        json_format: 是否使用JSON格式
    """
    # 移除默认处理器
    logger.remove()
    
    # 控制台输出格式
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    
    # 添加控制台处理器
    logger.add(
        sys.stdout,
        format=console_format,
        level=log_level,
        colorize=True,
    )
    
    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # 文件输出格式
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{message}"
    )
    
    # 添加文件处理器 - 普通日志
    logger.add(
        log_path / "aid-work-agent.log",
        format=file_format,
        level=log_level,
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
    )
    
    # 添加文件处理器 - 错误日志
    logger.add(
        log_path / "error.log",
        format=file_format,
        level="ERROR",
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
    )
    
    if json_format:
        # JSON格式日志（用于日志收集系统）
        logger.add(
            log_path / "aid-work-agent.json",
            format="{message}",
            level=log_level,
            rotation=rotation,
            retention=retention,
            serialize=True,
            encoding="utf-8",
        )
    
    logger.info(f"日志系统初始化完成，日志级别: {log_level}")


def get_logger(name: str = "aid-work-agent"):
    """
    获取日志记录器
    
    Args:
        name: 日志记录器名称
    
    Returns:
        Logger实例
    """
    return logger.bind(name=name)
