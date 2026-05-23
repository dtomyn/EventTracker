from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.db import connection_context, ensure_default_timeline_group
from app.schemas import EntryPayload
from app.services.entries import save_entry, validate_entry_form


class SimpleForm:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def getlist(self, key: str) -> list[Any]:
        val = self._data.get(key)
        if val is None:
            return []
        if isinstance(val, list):
            return val
        return [val]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add an entry to EventTracker DB.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--month", required=True, type=int)
    parser.add_argument("--day", type=int)
    parser.add_argument("--final-text", required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--tags")
    parser.add_argument("--links-json", help='JSON array of {"url":"...","note":"..."}')
    parser.add_argument("--link", action="append", help="url|note (can be repeated)")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def build_form_from_args(args: argparse.Namespace) -> SimpleForm:
    # build base values expected by validate_entry_form
    data: dict[str, Any] = {
        "event_year": str(args.year),
        "event_month": str(args.month),
        "event_day": "" if args.day is None else str(args.day),
        "group_id": str(args.group_id),
        "title": args.title or "",
        "source_url": args.source_url or "",
        "generated_text": "",
        "final_text": args.final_text or "",
        "tags": args.tags or "",
    }

    # assemble link_url and link_note lists from --links-json and --link
    urls: list[str] = []
    notes: list[str] = []

    if args.links_json:
        try:
            parsed = json.loads(args.links_json)
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    urls.append(item.get("url", "") or "")
                    notes.append(item.get("note", "") or "")
        except Exception:
            raise SystemExit("Invalid --links-json value; must be a JSON array.")

    if args.link:
        for raw in args.link:
            if raw is None:
                continue
            parts = raw.split("|", 1)
            url = parts[0].strip() if parts else ""
            note = parts[1].strip() if len(parts) > 1 else ""
            urls.append(url)
            notes.append(note)

    if urls:
        data["link_url"] = urls
    if notes:
        data["link_note"] = notes

    return SimpleForm(data)


def main() -> None:
    workspace_root = Path(__file__).resolve().parents[1]
    load_dotenv(workspace_root / ".env", override=True)

    args = parse_args()

    # resolve 'default' group id if requested
    group_id_value = args.group_id
    if str(group_id_value).lower() == "default":
        with connection_context() as conn:
            resolved = ensure_default_timeline_group(conn)
        group_id_value = str(resolved)

    # build form and validate
    # ensure group_id in args is numeric string at this point
    args_for_form = args
    args_for_form.group_id = group_id_value

    form = build_form_from_args(args_for_form)
    state, payload = validate_entry_form(form)
    if state.errors:
        print("Validation errors:")
        for k, v in state.errors.items():
            print(f"- {k}: {v}")
        raise SystemExit(2)

    if payload is None:
        print("Validation failed; no payload produced.")
        raise SystemExit(2)

    if args.dry_run:
        try:
            print(json.dumps(asdict(payload), indent=2))
        except Exception:
            print(repr(payload))
        return

    # persist
    with connection_context() as conn:
        entry_id = save_entry(conn, payload)
    print(entry_id)


if __name__ == "__main__":
    main()
