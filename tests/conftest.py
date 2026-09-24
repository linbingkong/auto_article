import os

# 旧 Web API 测试显式启用测试专用管理员绕过；生产环境默认不存在该变量。
os.environ["AUTH_TEST_BYPASS"] = "1"
# 无论开发机 config/.env 是否启用 MySQL，自动化测试必须使用隔离的临时 SQLite。
os.environ["DATABASE_BACKEND"] = "sqlite"