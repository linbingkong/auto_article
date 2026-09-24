"""CLI 入口。

用法：
    python -m wechat_agent.main run [--dry-run] [--articles N] [--sources weibo,zhihu]
    python -m wechat_agent.main schedule
    python -m wechat_agent.main check-config
    python -m wechat_agent.main history --limit 20
    python -m wechat_agent.main web --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config, PROJECT_ROOT


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline

    cfg = Config.load()
    pipeline = Pipeline(cfg)
    result = pipeline.run(
        max_articles=args.articles,
        dry_run=args.dry_run,
        sources=args.sources.split(",") if args.sources else None,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.errors:
        print("\n⚠️ 有错误发生：", file=sys.stderr)
        for e in result.errors:
            print(f"  - {e}", file=sys.stderr)
        return 2
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    from .scheduler import run_daily

    cfg = Config.load()
    if not cfg.schedule.enabled:
        print("schedule.enabled=false，未启动调度。请先在 config/config.yaml 开启。")
        return 1
    pipeline = Pipeline(cfg)
    job = lambda: pipeline.run(dry_run=args.dry_run)
    run_daily(job, cfg.schedule, block=True)
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    cfg = Config.load()
    print("=== 配置检查 ===")
    print(f"公众号: app_id={cfg.wechat.app_id or '(未设置)'!r} "
          f"publish_mode={cfg.wechat.publish_mode}")
    print(f"LLM: base_url={cfg.llm.base_url} model={cfg.llm.model} "
          f"api_key={'已设置' if cfg.llm.api_key else '(未设置)'}")
    print(f"配图: provider={cfg.image.provider}")
    print(f"热点源: {cfg.hot_sources}")
    print(f"选题过滤: enabled={cfg.topic_filter.enabled} "
          f"max_topics={cfg.topic_filter.max_topics} min_score={cfg.topic_filter.min_score}")
    if cfg.topic_filter.whitelist:
        print(f"白名单: {cfg.topic_filter.whitelist}")
    if cfg.topic_filter.blacklist:
        print(f"黑名单: {cfg.topic_filter.blacklist}")
    print(f"通知: enabled={cfg.notify.enabled} type={cfg.notify.webhook_type}")
    print(f"调度: enabled={cfg.schedule.enabled} time={cfg.schedule.daily_time}")
    print(f"输出目录: {cfg.output_path}")
    print(f"配置路径: {PROJECT_ROOT / 'config' / 'config.yaml'}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from .database import database_target_from_env
    from .history import HistoryStore

    cfg = Config.load()
    store = HistoryStore(database_target_from_env(cfg.output_path / "history.db"))
    print(json.dumps(store.latest(args.limit), ensure_ascii=False, indent=2))
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("需要安装 Web 依赖：pip install fastapi uvicorn", file=sys.stderr)
        return 1
    uvicorn.run(
        "wechat_agent.web_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="debug" if args.verbose else "info",
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wechat-agent", description="公众号自动发文智能体")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="执行一次完整流水线")
    p_run.add_argument("--dry-run", action="store_true", help="不调用公众号API，仅生成本地文件")
    p_run.add_argument("--articles", type=int, default=1, help="生成文章数（默认1）")
    p_run.add_argument("--sources", type=str, default=None, help="热点源，逗号分隔: weibo,zhihu,toutiao,cls,bili,baidu")

    p_sch = sub.add_parser("schedule", help="以守护进程方式定时运行")
    p_sch.add_argument("--dry-run", action="store_true")

    sub.add_parser("check-config", help="检查配置")
    p_history = sub.add_parser("history", help="查看最近运行/草稿记录")
    p_history.add_argument("--limit", type=int, default=20)

    p_web = sub.add_parser("web", help="启动 Web 管理台")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8000)
    p_web.add_argument("--reload", action="store_true", help="开发模式自动重载")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "run":
        return cmd_run(args)
    if args.command == "schedule":
        return cmd_schedule(args)
    if args.command == "check-config":
        return cmd_check_config(args)
    if args.command == "history":
        return cmd_history(args)
    if args.command == "web":
        return cmd_web(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())


