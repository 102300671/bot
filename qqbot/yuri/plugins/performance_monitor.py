#!/usr/bin/env python3
"""
性能监控模块
监控并发处理性能、响应时间和资源使用情况
"""

import time
import asyncio
import logging
from typing import Dict, List, Optional, Callable, Any
from collections import defaultdict, deque
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import threading
import psutil
import os

@dataclass
class PerformanceMetrics:
    """性能指标数据类"""
    operation_name: str
    start_time: float
    end_time: float
    success: bool
    error_message: Optional[str] = None
    user_id: Optional[str] = None
    group_id: Optional[str] = None
    
    @property
    def duration(self) -> float:
        """操作持续时间（秒）"""
        return self.end_time - self.start_time

class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self, max_records: int = 1000, cleanup_interval: int = 300):
        self.max_records = max_records
        self.cleanup_interval = cleanup_interval
        self.metrics: deque = deque(maxlen=max_records)
        self.operation_stats: Dict[str, Dict] = defaultdict(lambda: {
            'count': 0,
            'success_count': 0,
            'total_duration': 0.0,
            'min_duration': float('inf'),
            'max_duration': 0.0,
            'error_count': 0
        })
        self._lock = threading.Lock()
        self._last_cleanup = time.time()
    
    def record_operation(self, metrics: PerformanceMetrics):
        """记录操作性能指标"""
        with self._lock:
            current_time = time.time()
            
            # 定期清理
            if current_time - self._last_cleanup > self.cleanup_interval:
                self._cleanup_old_records()
                self._last_cleanup = current_time
            
            # 添加到指标列表
            self.metrics.append(metrics)
            
            # 更新统计信息
            stats = self.operation_stats[metrics.operation_name]
            stats['count'] += 1
            stats['total_duration'] += metrics.duration
            stats['min_duration'] = min(stats['min_duration'], metrics.duration)
            stats['max_duration'] = max(stats['max_duration'], metrics.duration)
            
            if metrics.success:
                stats['success_count'] += 1
            else:
                stats['error_count'] += 1
    
    def _cleanup_old_records(self):
        """清理旧记录"""
        current_time = time.time()
        cutoff_time = current_time - self.cleanup_interval * 2
        
        # 清理超过2倍清理间隔的记录
        while self.metrics and self.metrics[0].end_time < cutoff_time:
            self.metrics.popleft()
    
    def get_operation_stats(self, operation_name: Optional[str] = None) -> Dict:
        """获取操作统计信息"""
        with self._lock:
            if operation_name:
                return self.operation_stats.get(operation_name, {})
            return dict(self.operation_stats)
    
    def get_recent_metrics(self, minutes: int = 5) -> List[PerformanceMetrics]:
        """获取最近的性能指标"""
        cutoff_time = time.time() - minutes * 60
        return [m for m in self.metrics if m.end_time >= cutoff_time]
    
    def get_average_response_time(self, operation_name: Optional[str] = None) -> float:
        """获取平均响应时间"""
        stats = self.get_operation_stats(operation_name)
        if not stats:
            return 0.0
        
        total_count = sum(s['count'] for s in stats.values())
        total_duration = sum(s['total_duration'] for s in stats.values())
        
        return total_duration / total_count if total_count > 0 else 0.0
    
    def get_success_rate(self, operation_name: Optional[str] = None) -> float:
        """获取成功率"""
        stats = self.get_operation_stats(operation_name)
        if not stats:
            return 0.0
        
        total_count = sum(s['count'] for s in stats.values())
        success_count = sum(s['success_count'] for s in stats.values())
        
        return success_count / total_count if total_count > 0 else 0.0

@asynccontextmanager
async def monitor_performance(monitor: PerformanceMonitor, operation_name: str, 
                            user_id: Optional[str] = None, group_id: Optional[str] = None):
    """性能监控上下文管理器"""
    start_time = time.time()
    success = False
    error_message = None
    
    try:
        yield
        success = True
    except Exception as e:
        error_message = str(e)
        raise
    finally:
        end_time = time.time()
        metrics = PerformanceMetrics(
            operation_name=operation_name,
            start_time=start_time,
            end_time=end_time,
            success=success,
            error_message=error_message,
            user_id=user_id,
            group_id=group_id
        )
        monitor.record_operation(metrics)

def monitor_function(monitor: PerformanceMonitor, operation_name: str):
    """函数性能监控装饰器"""
    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                async with monitor_performance(monitor, operation_name):
                    return await func(*args, **kwargs)
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                success = False
                error_message = None
                
                try:
                    result = func(*args, **kwargs)
                    success = True
                    return result
                except Exception as e:
                    error_message = str(e)
                    raise
                finally:
                    end_time = time.time()
                    metrics = PerformanceMetrics(
                        operation_name=operation_name,
                        start_time=start_time,
                        end_time=end_time,
                        success=success,
                        error_message=error_message
                    )
                    monitor.record_operation(metrics)
            return sync_wrapper
    return decorator

class SystemMonitor:
    """系统资源监控器"""
    
    def __init__(self):
        self.process = psutil.Process(os.getpid())
    
    def get_memory_usage(self) -> Dict[str, float]:
        """获取内存使用情况"""
        memory_info = self.process.memory_info()
        return {
            'rss_mb': memory_info.rss / 1024 / 1024,  # 物理内存
            'vms_mb': memory_info.vms / 1024 / 1024,  # 虚拟内存
            'percent': self.process.memory_percent()
        }
    
    def get_cpu_usage(self) -> float:
        """获取CPU使用率"""
        return self.process.cpu_percent()
    
    def get_system_info(self) -> Dict[str, Any]:
        """获取系统信息"""
        return {
            'cpu_count': psutil.cpu_count(),
            'memory_total_gb': psutil.virtual_memory().total / 1024 / 1024 / 1024,
            'memory_available_gb': psutil.virtual_memory().available / 1024 / 1024 / 1024,
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage_percent': psutil.disk_usage('/').percent
        }

# 全局性能监控器实例
performance_monitor = PerformanceMonitor()
system_monitor = SystemMonitor()

def log_performance_summary():
    """记录性能摘要"""
    stats = performance_monitor.get_operation_stats()
    if not stats:
        return
    
    logging.info("=== 性能监控摘要 ===")
    for operation, stat in stats.items():
        avg_duration = stat['total_duration'] / stat['count'] if stat['count'] > 0 else 0
        success_rate = stat['success_count'] / stat['count'] * 100 if stat['count'] > 0 else 0
        
        logging.info(f"{operation}:")
        logging.info(f"  总调用次数: {stat['count']}")
        logging.info(f"  成功率: {success_rate:.1f}%")
        logging.info(f"  平均响应时间: {avg_duration:.3f}秒")
        logging.info(f"  最小响应时间: {stat['min_duration']:.3f}秒")
        logging.info(f"  最大响应时间: {stat['max_duration']:.3f}秒")
        logging.info(f"  错误次数: {stat['error_count']}")
    
    # 系统资源信息
    memory_info = system_monitor.get_memory_usage()
    system_info = system_monitor.get_system_info()
    
    logging.info("=== 系统资源信息 ===")
    logging.info(f"内存使用: {memory_info['rss_mb']:.1f}MB (物理) / {memory_info['vms_mb']:.1f}MB (虚拟)")
    logging.info(f"CPU使用率: {system_monitor.get_cpu_usage():.1f}%")
    logging.info(f"系统内存: {system_info['memory_available_gb']:.1f}GB 可用 / {system_info['memory_total_gb']:.1f}GB 总计")
    logging.info(f"磁盘使用率: {system_info['disk_usage_percent']:.1f}%")

async def periodic_performance_log():
    """定期记录性能信息"""
    while True:
        try:
            await asyncio.sleep(300)  # 每5分钟记录一次
            log_performance_summary()
        except Exception as e:
            logging.error(f"记录性能信息时出错: {e}")
