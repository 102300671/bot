#!/usr/bin/env python3
"""
并发处理工具模块
提供优化的并发控制、连接池管理和错误处理
"""

import asyncio
import time
import logging
import weakref
from typing import Dict, Optional, Any, Callable, TypeVar, Awaitable
from functools import wraps
from contextlib import asynccontextmanager
import aiomysql
from collections import defaultdict
import threading

# 类型变量
T = TypeVar('T')

class OptimizedLockManager:
    """优化的锁管理器，支持自动清理和性能监控"""
    
    def __init__(self, max_locks: int = 2000, cleanup_interval: int = 300):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_usage: Dict[str, float] = {}  # 记录锁的使用时间
        self._max_locks = max_locks
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()
        self._lock = threading.Lock()  # 保护锁字典的线程锁
        
    def get_lock(self, key: str) -> asyncio.Lock:
        """获取或创建锁"""
        with self._lock:
            current_time = time.time()
            
            # 定期清理
            if current_time - self._last_cleanup > self._cleanup_interval:
                self._cleanup_locks()
                self._last_cleanup = current_time
            
            if key not in self._locks:
                # 如果锁数量过多，清理最旧的锁
                if len(self._locks) >= self._max_locks:
                    self._cleanup_oldest_locks()
                
                self._locks[key] = asyncio.Lock()
                self._lock_usage[key] = current_time
            
            return self._locks[key]
    
    def _cleanup_locks(self):
        """清理长时间未使用的锁"""
        current_time = time.time()
        keys_to_remove = []
        
        for key, last_used in self._lock_usage.items():
            if current_time - last_used > self._cleanup_interval * 2:  # 超过清理间隔2倍时间
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self._locks[key]
            del self._lock_usage[key]
        
        if keys_to_remove:
            logging.info(f"清理了 {len(keys_to_remove)} 个未使用的锁")
    
    def _cleanup_oldest_locks(self):
        """清理最旧的锁"""
        if len(self._locks) < self._max_locks // 2:
            return
        
        # 按使用时间排序，删除最旧的25%
        sorted_keys = sorted(self._lock_usage.items(), key=lambda x: x[1])
        keys_to_remove = [key for key, _ in sorted_keys[:len(sorted_keys) // 4]]
        
        for key in keys_to_remove:
            del self._locks[key]
            del self._lock_usage[key]
        
        logging.info(f"清理了 {len(keys_to_remove)} 个最旧的锁")

class ConnectionPoolManager:
    """连接池管理器，支持自动重连和健康检查"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._pool: Optional[aiomysql.Pool] = None
        self._pool_lock = asyncio.Lock()
        self._health_check_interval = 60  # 健康检查间隔
        self._last_health_check = 0
        
    async def get_pool(self) -> aiomysql.Pool:
        """获取连接池，如果不存在则创建"""
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:  # 双重检查
                    self._pool = await aiomysql.create_pool(**self.config)
                    logging.info("数据库连接池已创建")
        
        # 定期健康检查
        current_time = time.time()
        if current_time - self._last_health_check > self._health_check_interval:
            await self._health_check()
            self._last_health_check = current_time
        
        return self._pool
    
    async def _health_check(self):
        """检查连接池健康状态"""
        if not self._pool:
            return
        
        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchone()
        except Exception as e:
            logging.warning(f"连接池健康检查失败: {e}")
            await self._recreate_pool()
    
    async def _recreate_pool(self):
        """重新创建连接池"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
        
        try:
            self._pool = await aiomysql.create_pool(**self.config)
            logging.info("数据库连接池已重新创建")
        except Exception as e:
            logging.error(f"重新创建连接池失败: {e}")
            self._pool = None
    
    async def close(self):
        """关闭连接池"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

class RateLimiter:
    """速率限制器，防止API调用过于频繁"""
    
    def __init__(self, max_calls: int = 10, time_window: float = 60.0):
        self.max_calls = max_calls
        self.time_window = time_window
        self._calls: Dict[str, list] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def acquire(self, key: str) -> bool:
        """尝试获取许可"""
        async with self._lock:
            current_time = time.time()
            calls = self._calls[key]
            
            # 清理过期的调用记录
            calls[:] = [call_time for call_time in calls if current_time - call_time < self.time_window]
            
            if len(calls) >= self.max_calls:
                return False
            
            calls.append(current_time)
            return True
    
    async def wait_for_permission(self, key: str, timeout: float = 30.0) -> bool:
        """等待直到获得许可"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if await self.acquire(key):
                return True
            await asyncio.sleep(1.0)
        return False

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 10.0,
    exponential_base: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """指数退避重试装饰器"""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            delay = base_delay
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logging.warning(f"操作失败，重试第{attempt + 1}次: {e}")
                        await asyncio.sleep(delay)
                        delay = min(delay * exponential_base, max_delay)
                    else:
                        logging.error(f"操作最终失败: {e}")
                        raise last_exception
            
            raise last_exception
        return wrapper
    return decorator

@asynccontextmanager
async def db_transaction(pool_manager: ConnectionPoolManager):
    """数据库事务上下文管理器"""
    pool = await pool_manager.get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            try:
                yield conn, cursor
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

class ConcurrentTaskManager:
    """并发任务管理器，控制同时执行的任务数量"""
    
    def __init__(self, max_concurrent: int = 10):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
    
    async def execute(self, coro: Awaitable[T]) -> T:
        """执行协程，受并发限制控制"""
        async with self.semaphore:
            self.active_tasks += 1
            try:
                result = await coro
                self.completed_tasks += 1
                return result
            except Exception as e:
                self.failed_tasks += 1
                raise
            finally:
                self.active_tasks -= 1
    
    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        return {
            'active_tasks': self.active_tasks,
            'completed_tasks': self.completed_tasks,
            'failed_tasks': self.failed_tasks,
            'max_concurrent': self.semaphore._value
        }

# 全局实例
lock_manager = OptimizedLockManager()
task_manager = ConcurrentTaskManager(max_concurrent=20)

def get_user_lock(user_id: str, group_id: str) -> asyncio.Lock:
    """获取用户锁的便捷函数"""
    return lock_manager.get_lock(f"{user_id}_{group_id}")

def get_group_lock(group_id: str) -> asyncio.Lock:
    """获取群组锁的便捷函数"""
    return lock_manager.get_lock(f"group_{group_id}")

# 导出列表
__all__ = [
    'OptimizedLockManager',
    'ConnectionPoolManager', 
    'RateLimiter',
    'retry_with_backoff',
    'db_transaction',
    'ConcurrentTaskManager',
    'task_manager',
    'get_user_lock',
    'get_group_lock',
    'lock_manager'
]
