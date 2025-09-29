#!/usr/bin/env python3
"""
签到插件数据库迁移脚本
将现有的单群组数据迁移到多群组分离结构
"""

import pymysql
import logging

# 数据库配置
DB_CONFIG = {
    'host': '192.168.159.83',
    'user': 'signin',
    'password': 'signin',
    'db': 'nonebot_signin',
    'charset': 'utf8mb4'
}

def migrate_database():
    """迁移数据库结构"""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            
            # 检查是否需要迁移
            cursor.execute("SHOW TABLES LIKE 'users'")
            if not cursor.fetchone():
                print("数据库表不存在，无需迁移")
                return
            
            # 检查users表是否已经有group_id字段
            cursor.execute("DESCRIBE users")
            columns = [col[0] for col in cursor.fetchall()]
            
            if 'group_id' in columns:
                print("数据库已经是新结构，无需迁移")
                return
            
            print("开始迁移数据库...")
            
            # 1. 备份现有数据
            print("备份现有数据...")
            
            # 备份users表
            cursor.execute("CREATE TABLE users_backup AS SELECT * FROM users")
            
            # 备份sign_records表
            cursor.execute("CREATE TABLE sign_records_backup AS SELECT * FROM sign_records")
            
            # 备份points_history表
            cursor.execute("CREATE TABLE points_history_backup AS SELECT * FROM points_history")
            
            # 2. 删除现有表
            print("删除现有表...")
            cursor.execute("DROP TABLE IF EXISTS points_history")
            cursor.execute("DROP TABLE IF EXISTS sign_records")
            cursor.execute("DROP TABLE IF EXISTS users")
            
            # 3. 创建新表结构
            print("创建新表结构...")
            
            # 创建用户表（按群组分离）
            cursor.execute('''
                CREATE TABLE users (
                    user_id VARCHAR(50),
                    group_id VARCHAR(50),
                    username VARCHAR(100),
                    total_points INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, group_id)
                )
            ''')
            
            # 创建签到记录表（按群组分离）
            cursor.execute('''
                CREATE TABLE sign_records (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(50),
                    group_id VARCHAR(50),
                    sign_date DATE NOT NULL,
                    points_earned INT DEFAULT 0,
                    continuous_days INT DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_sign (user_id, group_id, sign_date),
                    FOREIGN KEY (user_id, group_id) REFERENCES users(user_id, group_id) ON DELETE CASCADE
                )
            ''')
            
            # 创建积分流水表（按群组分离）
            cursor.execute('''
                CREATE TABLE points_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(50),
                    group_id VARCHAR(50),
                    points_change INT NOT NULL,
                    reason VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id, group_id) REFERENCES users(user_id, group_id) ON DELETE CASCADE
                )
            ''')
            
            # 4. 迁移数据（假设默认群组ID为'default'）
            print("迁移数据...")
            default_group_id = 'default'
            
            # 迁移users表数据
            cursor.execute('''
                INSERT INTO users (user_id, group_id, username, total_points, created_at)
                SELECT user_id, %s, username, total_points, created_at
                FROM users_backup
            ''', (default_group_id,))
            
            # 迁移sign_records表数据
            cursor.execute('''
                INSERT INTO sign_records (user_id, group_id, sign_date, points_earned, continuous_days, created_at)
                SELECT user_id, %s, sign_date, points_earned, continuous_days, created_at
                FROM sign_records_backup
            ''', (default_group_id,))
            
            # 迁移points_history表数据
            cursor.execute('''
                INSERT INTO points_history (user_id, group_id, points_change, reason, created_at)
                SELECT user_id, %s, points_change, reason, created_at
                FROM points_history_backup
            ''', (default_group_id,))
            
            # 5. 提交更改
            connection.commit()
            
            print("数据库迁移完成！")
            print(f"所有现有数据已迁移到默认群组ID: {default_group_id}")
            print("备份表已保留，如需清理请手动删除：")
            print("- users_backup")
            print("- sign_records_backup") 
            print("- points_history_backup")
            
    except Exception as e:
        print(f"迁移失败: {e}")
        connection.rollback()
    finally:
        if connection:
            connection.close()

if __name__ == "__main__":
    print("签到插件数据库迁移工具")
    print("=" * 50)
    
    response = input("此操作将修改数据库结构，是否继续？(y/N): ")
    if response.lower() == 'y':
        migrate_database()
    else:
        print("迁移已取消")
