import os

print("=== 环境变量检查 ===")
print(f"POSTGRES_URL: {os.getenv('POSTGRES_URL', '未设置')}")
print(f"APP_ENV: {os.getenv('APP_ENV', '未设置')}")
print(f"DATABASE_URL: {os.getenv('DATABASE_URL', '未设置')}")

from shared.config import settings
print(f"\n=== Settings配置 ===")
print(f"postgres_url: {settings.postgres_url}")
