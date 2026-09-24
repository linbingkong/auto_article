from pathlib import Path

from wechat_agent.database import MySQLConfig, _translate_mysql_sql, backend_name, database_target_from_env


def test_mysql_sql_translation_handles_placeholders_and_ignore():
    sql = _translate_mysql_sql("INSERT OR IGNORE INTO accounts(user_id) VALUES(?)")
    assert sql == "INSERT IGNORE INTO accounts(user_id) VALUES(%s)"


def test_mysql_sql_translation_handles_upsert_and_scalar_max():
    sql = _translate_mysql_sql(
        "INSERT INTO accounts(user_id,trial_total) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "trial_total=MAX(trial_total,excluded.trial_total)"
    )
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "GREATEST(trial_total,VALUES(trial_total))" in sql
    assert "?" not in sql


def test_mysql_sql_translation_handles_nonnegative_updates():
    sql = _translate_mysql_sql("UPDATE accounts SET paid_reserved=MAX(0,paid_reserved-1) WHERE user_id=?")
    assert "GREATEST(0,paid_reserved-1)" in sql
    assert sql.endswith("user_id=%s")


def test_database_target_defaults_to_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    expected = Path("output/_database_test/auth.db")
    target = database_target_from_env(expected)
    assert target == expected
    assert backend_name(target) == "sqlite"


def test_database_target_reads_mysql_without_leaking_password(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "mysql")
    monkeypatch.setenv("MYSQL_HOST", "db.example.test")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USER", "app")
    monkeypatch.setenv("MYSQL_PASSWORD", "top-secret")
    monkeypatch.setenv("MYSQL_DATABASE", "wechat_prod")
    target = database_target_from_env(Path("unused.db"))
    assert isinstance(target, MySQLConfig)
    assert target.port == 3307
    assert target.database == "wechat_prod"
    assert backend_name(target) == "mysql"
    assert "top-secret" not in repr(target)
