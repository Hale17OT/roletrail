"""Telegram entry point; public preview by default, optional user-authorized MTProto."""
import argparse
import json

from discovery.pipeline import load_config, run_discovery
from discovery.store import LeadStore
from discovery.http import Fetcher
from discovery.telegram import fetch_preview, login, parse_post


def fetch_channel_posts(channel_handle, max_pages=30, max_days=5):
    if max_days != 5:
        raise ValueError("Telegram discovery uses the strict five-day ceiling")
    store = LeadStore()
    posts, _ = fetch_preview(channel_handle.lstrip("@"), Fetcher(store), store, max_pages)
    return posts


def harvest_telegram_jobs(channels_local=None, channels_remote=None, status="ready"):
    config = load_config()
    if channels_local is not None or channels_remote is not None:
        handles = list(dict.fromkeys((channels_local or []) + (channels_remote or [])))
        config["telegram"]["channels"] = [{"handle": ch.lstrip("@"), "ownership": "user_configured"} for ch in handles]
        config["telegram"]["channels_per_run"] = len(handles)
    jobs, _ = run_discovery(sources=["telegram"], status=status, config=config)
    return jobs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="Explicit interactive login for optional Telethon adapter")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--adapter", choices=["preview", "mtproto"], default=None)
    parser.add_argument("--connection-status", action="store_true")
    args = parser.parse_args()
    if args.connection_status:
        from discovery.telegram_auth import connection_status
        print(json.dumps(connection_status(), indent=2))
    elif args.login:
        login()
    else:
        config = load_config()
        if args.adapter:
            config["telegram"]["adapter"] = args.adapter
        _, report = run_discovery(sources=["telegram"], sync=args.sync, config=config)
        print(json.dumps(report, indent=2, ensure_ascii=False))
