import argparse
import asyncio
import json

from .config import Settings


def main():
    parser = argparse.ArgumentParser(prog="codiscover-video")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动本地工作台和离线 worker")
    serve.add_argument("--port", type=int, default=8765)
    sub.add_parser("doctor", help="检查当前模式、配置与平台连接（不输出凭据）")
    sub.add_parser("worker", help="只运行离线 worker；勿与 serve 同时启动")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "codiscover_video.app:create_app",
            factory=True,
            host="127.0.0.1",
            port=args.port,
            log_level="info",
            access_log=False,
        )
    elif args.command == "doctor":
        asyncio.run(doctor())
    else:
        asyncio.run(worker())


async def doctor():
    from .app import create_app

    settings = Settings.from_env()
    app = create_app(settings, start_worker=False)
    result = {
        "provider_mode": settings.provider_mode,
        "llm_mode": settings.llm_mode,
        "api_key_configured": bool(settings.api_key.get_secret_value()),
        "credential_file_exists": settings.credential_path.is_file(),
        "database": str(settings.db_path),
    }
    try:
        result["provider"] = await app.state.service.provider.health()
    except Exception as exc:
        result["provider"] = {"error": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def worker():
    from .app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        await asyncio.Event().wait()


if __name__ == "__main__":
    main()
