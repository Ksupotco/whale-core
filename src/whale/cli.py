"""whale CLI: serve / token / doctor."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    env = os.environ.get("WHALE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".whale"


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import App
    from .server.http import create_server

    app = App(args.data)
    host = args.host or app.config.value.server.host
    port = args.port or app.config.value.server.port
    print(f"\n  🐋 Whale {app.version}  ·  data: {app.data_dir}")
    print(f"  控制台   http://{host}:{port}/?token={app.config.api_token}")
    print(f"  访问令牌 {app.config.api_token}（打开上面的链接即可登录，令牌会保存在浏览器里）\n")
    uvicorn.run(create_server(app), host=host, port=port, log_level=args.log_level, ws="websockets"
                if _has("websockets") else "auto")
    return 0


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def cmd_token(args: argparse.Namespace) -> int:
    from .kernel.config import Config

    cfg = Config(args.data)
    if args.rotate:
        import secrets

        cfg.set_secret("api_token", secrets.token_urlsafe(24))
    print(cfg.api_token)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .app import App

    app = App(args.data)
    ok = True
    print(f"Whale {app.version} · Python {sys.version.split()[0]} · data {app.data_dir}")
    print(f"  SQLite FTS5: {'yes' if app.db.fts5 else 'no (keyword recall falls back to LIKE)'}")
    status = app.models.status()
    for role, info in status["roles"].items():
        mark = "✓" if info["configured"] else "·"
        print(f"  model {role:<9} {mark} {info['provider'] or '-'} {info['model'] or ''}")
    if status["roles"]["chat"]["configured"]:
        res = asyncio.run(app.models.test("chat"))
        print(f"  chat probe: {res}")
        ok = ok and res.get("ok", False)
    for mod in ("uvicorn", "starlette", "httpx", "pydantic", "websockets", "cryptography"):
        print(f"  {mod:<13} {'✓' if _has(mod) else '✗ missing'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="whale", description="Whale personal agent core")
    parser.add_argument("--data", type=Path, default=default_data_dir(), help="data directory (default ~/.whale)")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("serve", help="run the agent and console")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--log-level", default="info")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("token", help="print (or --rotate) the API token")
    p.add_argument("--rotate", action="store_true")
    p.set_defaults(fn=cmd_token)
    p = sub.add_parser("doctor", help="check environment and models")
    p.set_defaults(fn=cmd_doctor)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if "fn" not in vars(args):
        args = parser.parse_args([*(argv or []), "serve"]) if argv is not None else parser.parse_args(["serve"])
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
