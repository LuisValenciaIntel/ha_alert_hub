from __future__ import annotations

import argparse
import os
from pathlib import Path

from notifications_app import create_app
from notifications_app import db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Home Alert Hub PWA")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the web application")
    serve_parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    serve_parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5000")))
    serve_parser.add_argument(
        "--debug",
        action="store_true",
        default=os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes", "on"},
        help="Enable Flask debug mode.",
    )

    admin_parser = subparsers.add_parser("init-admin", help="Create or update an admin account")
    admin_parser.add_argument("--username", required=True)
    admin_parser.add_argument("--password", required=True)

    subparsers.add_parser("show-token", help="Print the Home Assistant automation token")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"
    host = getattr(args, "host", os.getenv("HOST", "0.0.0.0"))
    port = getattr(args, "port", int(os.getenv("PORT", "5000")))
    debug = getattr(
        args,
        "debug",
        os.getenv("FLASK_DEBUG", "false").lower() in {"1", "true", "yes", "on"},
    )

    if command == "serve":
        app = create_app()
        app.run(host=host, port=port, debug=debug)
        return 0

    app = create_app()
    database_path = Path(app.config["DATABASE_PATH"])

    if command == "init-admin":
        db.upsert_user(database_path, args.username, args.password)
        print(f"Admin user '{args.username}' updated successfully.")
        return 0

    if command == "show-token":
        print(app.config["HOME_ASSISTANT_API_TOKEN"])
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


