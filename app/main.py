from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import connection_context, init_db
from app.env import load_app_env
from app.services.ai_generate import (
    DraftGenerationConfigurationError,
    DraftGenerationError,
    generate_entry_suggestion,
    load_ai_provider,
)
from app.services.entries import (
    blank_form_state,
    build_timeline_groups,
    create_timeline_group,
    decode_timeline_cursor,
    delete_timeline_group,
    DEFAULT_TIMELINE_PAGE_SIZE,
    form_state_from_entry,
    format_plain_text,
    get_default_timeline_group,
    get_entry,
    get_timeline_group,
    list_timeline_entries_page,
    list_timeline_groups,
    list_timeline_entries,
    list_timeline_month_buckets,
    list_timeline_summary_groups,
    list_timeline_year_buckets,
    normalize_timeline_group_name,
    paginate_entries_in_memory,
    rename_timeline_group,
    sanitize_rich_text,
    sanitize_search_snippet,
    save_entry,
    TimelineGroupValidationError,
    update_entry,
    validate_entry_form,
)
from app.services.extraction import extract_url_text
from app.services.group_web_search import (
    clear_group_web_search_cache,
    get_group_web_search_request_timeout_ms,
    GroupWebSearchConfigurationError,
    GroupWebSearchError,
    GroupWebSearchTimeoutError,
    search_group_web,
)
from app.services.search import (
    DEFAULT_SEARCH_PAGE_SIZE,
    decode_search_cursor,
    filter_timeline_entries,
    paginate_search_results,
    search_entries,
)


load_app_env()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["plain_text"] = format_plain_text
templates.env.filters["render_entry_html"] = sanitize_rich_text
templates.env.filters["render_search_snippet"] = sanitize_search_snippet


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Events", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def timeline(request: Request, q: str = "", group_id: str = "") -> HTMLResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        entries, next_cursor, has_more = _list_timeline_details_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
            page_size=DEFAULT_TIMELINE_PAGE_SIZE,
        )

        context = {
            "request": request,
            "page_title": (
                f"{scope['selected_group'].name} Timeline"
                if scope["selected_group"]
                else "Timeline"
            ),
            "query": scope["normalized_query"],
            "is_filtered": bool(scope["normalized_query"]),
            "match_count": scope["match_count"],
            "timeline_filters": scope["timeline_filters"],
            "selected_group_id": scope["selected_group_id"],
            "selected_group_query_value": scope["selected_group_query_value"],
            "selected_group_name": (
                scope["selected_group"].name
                if scope["selected_group"]
                else "All groups"
            ),
            "timeline_groups": build_timeline_groups(entries),
            "details_has_more": has_more,
            "details_next_cursor": next_cursor,
            "timeline_web_search": scope["timeline_web_search"],
            "timeline_scope": {
                "scopeKey": scope["scope_key"],
                "groupId": scope["selected_group_id"],
                "query": scope["normalized_query"],
                "detailsEndpoint": "/timeline/details",
                "monthsEndpoint": "/timeline/months",
                "yearsEndpoint": "/timeline/years",
                "summariesEndpoint": "/timeline/summaries",
                "pageSize": DEFAULT_TIMELINE_PAGE_SIZE,
                "hasMore": has_more,
                "nextCursor": next_cursor,
                "groupWebSearch": {
                    "endpoint": "/timeline/group-web-search",
                    "refreshEndpoint": "/timeline/group-web-search/refresh",
                    "streamEndpoint": "/timeline/group-web-search/stream",
                    "requestTimeoutMs": get_group_web_search_request_timeout_ms(),
                    "groupId": scope["selected_group_id"],
                    "hasQuery": scope["timeline_web_search"]["show_panel"],
                    "providerIsCopilot": scope["timeline_web_search"][
                        "provider_is_copilot"
                    ],
                },
            },
        }
    return templates.TemplateResponse(request, "timeline.html", context)


@app.get("/timeline/group-web-search")
async def timeline_group_web_search(group_id: str = "") -> JSONResponse:
    selected_group_id = _parse_group_id(group_id)
    if selected_group_id is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    with connection_context() as connection:
        group = get_timeline_group(connection, selected_group_id)

    if group is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    if not group.web_search_query:
        return JSONResponse(
            {
                "enabled": False,
                "query": None,
                "items": [],
                "message": "No related results found right now.",
            }
        )

    if not _is_copilot_provider():
        return JSONResponse(
            {
                "enabled": False,
                "query": group.web_search_query,
                "items": [],
                "message": "Available when GitHub Copilot is the active AI provider.",
            }
        )

    try:
        result = await search_group_web(group.web_search_query)
    except GroupWebSearchConfigurationError:
        logging.getLogger(__name__).exception("Group web search configuration failed")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Could not load web results.",
            },
            status_code=502,
        )
    except GroupWebSearchTimeoutError:
        logging.getLogger(__name__).warning("Group web search timed out")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Web search timed out. Try again.",
            },
            status_code=504,
        )
    except GroupWebSearchError:
        logging.getLogger(__name__).exception("Group web search failed")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Could not load web results.",
            },
            status_code=502,
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected group web search failure")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Could not load web results.",
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "enabled": True,
            "query": result.query,
            "items": result.to_payload()["items"],
            "message": None if result.items else "No related results found right now.",
        }
    )


@app.get("/timeline/group-web-search/stream")
async def timeline_group_web_search_stream(
    group_id: str = "", force_refresh: bool = False
) -> StreamingResponse:
    selected_group_id = _parse_group_id(group_id)
    if selected_group_id is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    with connection_context() as connection:
        group = get_timeline_group(connection, selected_group_id)

    if group is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    async def stream_events() -> Any:
        if not group.web_search_query:
            yield _encode_sse_event(
                "result",
                {
                    "query": None,
                    "items": [],
                    "message": "No related results found right now.",
                },
            )
            yield _encode_sse_event("complete", {"ok": True})
            return

        if not _is_copilot_provider():
            yield _encode_sse_event(
                "search_error",
                {
                    "message": "Available when GitHub Copilot is the active AI provider.",
                },
            )
            yield _encode_sse_event("complete", {"ok": False})
            return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def on_event(payload: dict[str, Any]) -> None:
            queue.put_nowait(payload)

        async def run_search() -> None:
            try:
                result = await search_group_web(
                    group.web_search_query,
                    force_refresh=force_refresh,
                    event_sink=on_event,
                )
            except GroupWebSearchConfigurationError:
                logging.getLogger(__name__).exception(
                    "Group web search configuration failed"
                )
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Could not load web results.",
                    }
                )
            except GroupWebSearchTimeoutError:
                logging.getLogger(__name__).warning("Group web search timed out")
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Web search timed out. Try again.",
                    }
                )
            except GroupWebSearchError:
                logging.getLogger(__name__).exception("Group web search failed")
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Could not load web results.",
                    }
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Unexpected group web search failure"
                )
                on_event(
                    {
                        "kind": "search_error",
                        "message": "Could not load web results.",
                    }
                )
            else:
                on_event(
                    {
                        "kind": "result",
                        **result.to_payload(),
                        "message": None
                        if result.items
                        else "No related results found right now.",
                    }
                )
            finally:
                on_event({"kind": "complete", "ok": True})

        search_task = asyncio.create_task(run_search())
        try:
            while True:
                payload = await queue.get()
                event_name = str(payload.get("kind") or "message")
                yield _encode_sse_event(event_name, payload)
                if event_name == "complete":
                    break
        finally:
            if not search_task.done():
                search_task.cancel()
                try:
                    await search_task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/timeline/group-web-search/refresh")
async def refresh_timeline_group_web_search(group_id: str = "") -> JSONResponse:
    selected_group_id = _parse_group_id(group_id)
    if selected_group_id is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    with connection_context() as connection:
        group = get_timeline_group(connection, selected_group_id)

    if group is None:
        raise HTTPException(status_code=404, detail="Timeline group not found")

    if not group.web_search_query:
        return JSONResponse(
            {
                "enabled": False,
                "query": None,
                "items": [],
                "message": "No related results found right now.",
            }
        )

    if not _is_copilot_provider():
        return JSONResponse(
            {
                "enabled": False,
                "query": group.web_search_query,
                "items": [],
                "message": "Available when GitHub Copilot is the active AI provider.",
            }
        )

    try:
        result = await search_group_web(group.web_search_query, force_refresh=True)
    except GroupWebSearchConfigurationError:
        logging.getLogger(__name__).exception("Group web search configuration failed")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Could not load web results.",
            },
            status_code=502,
        )
    except GroupWebSearchTimeoutError:
        logging.getLogger(__name__).warning("Group web search timed out")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Web search timed out. Try again.",
            },
            status_code=504,
        )
    except GroupWebSearchError:
        logging.getLogger(__name__).exception("Group web search failed")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Could not load web results.",
            },
            status_code=502,
        )
    except Exception:
        logging.getLogger(__name__).exception("Unexpected group web search failure")
        return JSONResponse(
            {
                "enabled": True,
                "query": group.web_search_query,
                "items": [],
                "message": "Could not load web results.",
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "enabled": True,
            "query": result.query,
            "items": result.to_payload()["items"],
            "message": None if result.items else "No related results found right now.",
        }
    )


@app.get("/timeline/details")
def timeline_details(
    q: str = "",
    group_id: str = "",
    cursor: str = "",
    page_size: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        parsed_cursor = _parse_timeline_cursor(cursor)
        entries, next_cursor, has_more = _list_timeline_details_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
            page_size=page_size,
            cursor=parsed_cursor,
        )

    return JSONResponse(
        {
            "scope_key": scope["scope_key"],
            "items_html": _render_partial(
                "partials/timeline_detail_groups.html",
                timeline_groups=build_timeline_groups(entries),
            ),
            "has_more": has_more,
            "next_cursor": next_cursor,
            "loaded_count": len(entries),
        }
    )


@app.get("/timeline/years")
def timeline_years(q: str = "", group_id: str = "") -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        scoped_entries = _list_entries_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
        )
        buckets = list_timeline_year_buckets(scoped_entries)

    return JSONResponse(
        {
            "view": "years",
            "scope_key": scope["scope_key"],
            "total_entries": len(scoped_entries),
            "bucket_count": len(buckets),
            "items_html": _render_partial(
                "partials/timeline_bucket_cards.html",
                buckets=buckets,
            ),
        }
    )


@app.get("/timeline/months")
def timeline_months(
    q: str = "",
    group_id: str = "",
    year: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        scoped_entries = _list_entries_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
        )
        buckets = list_timeline_month_buckets(scoped_entries, year=year)

    return JSONResponse(
        {
            "view": "months",
            "scope_key": scope["scope_key"],
            "year": year,
            "bucket_count": len(buckets),
            "items_html": _render_partial(
                "partials/timeline_bucket_cards.html",
                buckets=buckets,
            ),
        }
    )


@app.get("/timeline/summaries")
def timeline_summaries(
    q: str = "",
    group_id: str = "",
    year: int | None = None,
    month: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_timeline_scope(connection, q=q, group_id=group_id)
        scoped_entries = _list_entries_for_scope(
            connection,
            normalized_query=scope["normalized_query"],
            selected_group_id=scope["selected_group_id"],
        )
        groups = list_timeline_summary_groups(scoped_entries, year=year, month=month)

    return JSONResponse(
        {
            "view": "events",
            "scope_key": scope["scope_key"],
            "year": year,
            "month": month,
            "group_count": len(groups),
            "items_html": _render_partial(
                "partials/timeline_summary_groups.html",
                timeline_groups=groups,
            ),
        }
    )


@app.get("/search", response_class=HTMLResponse)
def ranked_search(request: Request, q: str = "", group_id: str = "") -> HTMLResponse:
    with connection_context() as connection:
        scope = _load_group_scope(connection, q=q, group_id=group_id)
        all_results = (
            search_entries(
                connection,
                scope["normalized_query"],
                group_id=scope["selected_group_id"],
            )
            if scope["normalized_query"]
            else []
        )
        search_results, next_cursor, has_more = paginate_search_results(
            all_results,
            page_size=DEFAULT_SEARCH_PAGE_SIZE,
        )
        context = {
            "request": request,
            "page_title": (
                f"{scope['selected_group'].name} Search"
                if scope["selected_group"]
                else "Search"
            ),
            "query": scope["normalized_query"],
            "search_results": search_results,
            "has_query": bool(scope["normalized_query"]),
            "result_count": len(all_results),
            "initial_result_count": len(search_results),
            "search_has_more": has_more,
            "timeline_filters": scope["timeline_filters"],
            "selected_group_id": scope["selected_group_id"],
            "selected_group_query_value": scope["selected_group_query_value"],
            "selected_group_name": (
                scope["selected_group"].name
                if scope["selected_group"]
                else "All groups"
            ),
            "search_scope": {
                "scopeKey": scope["scope_key"],
                "groupId": scope["selected_group_id"],
                "groupName": scope["selected_group"].name
                if scope["selected_group"]
                else "All groups",
                "query": scope["normalized_query"],
                "resultsEndpoint": "/search/results",
                "pageSize": DEFAULT_SEARCH_PAGE_SIZE,
                "hasMore": has_more,
                "nextCursor": next_cursor,
                "totalCount": len(all_results),
                "loadedCount": len(search_results),
            },
        }
    return templates.TemplateResponse(request, "search.html", context)


@app.get("/search/results")
def ranked_search_results(
    q: str = "",
    group_id: str = "",
    cursor: str = "",
    page_size: int | None = None,
) -> JSONResponse:
    with connection_context() as connection:
        scope = _load_group_scope(connection, q=q, group_id=group_id)
        parsed_cursor = _parse_search_cursor(cursor)
        all_results = (
            search_entries(
                connection,
                scope["normalized_query"],
                group_id=scope["selected_group_id"],
            )
            if scope["normalized_query"]
            else []
        )
        search_results, next_cursor, has_more = paginate_search_results(
            all_results,
            page_size=page_size,
            cursor=parsed_cursor,
        )

    return JSONResponse(
        {
            "scope_key": scope["scope_key"],
            "items_html": _render_partial(
                "partials/search_results.html",
                search_results=search_results,
                show_empty_state=False,
            ),
            "has_more": has_more,
            "next_cursor": next_cursor,
            "loaded_count": len(search_results),
            "total_count": len(all_results),
        }
    )


@app.get("/visualization", response_class=HTMLResponse)
def timeline_visualization(request: Request) -> HTMLResponse:
    return RedirectResponse(url="/", status_code=307)


@app.get("/entries/export")
def export_entries() -> JSONResponse:
    with connection_context() as connection:
        entries = list_timeline_entries(connection)

    exported_entries = []
    for entry in entries:
        data = asdict(entry)
        data.pop("preview_text", None)
        exported_entries.append(data)

    export_timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    file_name = f"EventTracker-export-{export_timestamp}.json"

    return JSONResponse(
        {
            "count": len(exported_entries),
            "entries": exported_entries,
        },
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@app.get("/entries/new", response_class=HTMLResponse)
def new_entry_form(request: Request) -> HTMLResponse:
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)

    form_state = blank_form_state()
    if timeline_filters:
        form_state.values["group_id"] = str(timeline_filters[0].id)

    return templates.TemplateResponse(
        request,
        "entry_form.html",
        {
            "page_title": "New Entry",
            "form_state": form_state,
            "entry_id": None,
            "timeline_filters": timeline_filters,
        },
    )


@app.get("/entries/{entry_id:int}/view", response_class=HTMLResponse)
def view_entry(request: Request, entry_id: int) -> HTMLResponse:
    with connection_context() as connection:
        entry = get_entry(connection, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    return templates.TemplateResponse(
        request,
        "entry_detail.html",
        {
            "page_title": entry.title or "Entry",
            "entry": entry,
        },
    )


@app.post("/entries/new", response_model=None)
async def create_entry(request: Request) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    form_state, payload = validate_entry_form(form)
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        if (
            payload is not None
            and get_timeline_group(connection, payload.group_id) is None
        ):
            form_state.errors["group_id"] = "Select an existing timeline group."
            payload = None

    if payload is None:
        return templates.TemplateResponse(
            request,
            "entry_form.html",
            {
                "page_title": "New Entry",
                "form_state": form_state,
                "entry_id": None,
                "timeline_filters": timeline_filters,
            },
            status_code=400,
        )

    with connection_context() as connection:
        entry_id = save_entry(connection, payload)
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)


@app.get("/entries/{entry_id:int}", response_class=HTMLResponse)
def edit_entry_form(request: Request, entry_id: int) -> HTMLResponse:
    with connection_context() as connection:
        entry = get_entry(connection, entry_id)
        timeline_filters = list_timeline_groups(connection)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    return templates.TemplateResponse(
        request,
        "entry_form.html",
        {
            "page_title": "Edit Entry",
            "form_state": form_state_from_entry(entry),
            "entry_id": entry_id,
            "timeline_filters": timeline_filters,
        },
    )


@app.post("/entries/{entry_id:int}", response_model=None)
async def update_entry_route(
    request: Request, entry_id: int
) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    form_state, payload = validate_entry_form(form)
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        if (
            payload is not None
            and get_timeline_group(connection, payload.group_id) is None
        ):
            form_state.errors["group_id"] = "Select an existing timeline group."
            payload = None

    if payload is None:
        return templates.TemplateResponse(
            request,
            "entry_form.html",
            {
                "page_title": "Edit Entry",
                "form_state": form_state,
                "entry_id": entry_id,
                "timeline_filters": timeline_filters,
            },
            status_code=400,
        )

    with connection_context() as connection:
        existing = get_entry(connection, entry_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        update_entry(connection, entry_id, payload)
    return RedirectResponse(url=f"/entries/{entry_id}/view", status_code=303)


@app.get("/admin/groups", response_class=HTMLResponse)
def manage_groups(
    request: Request,
    notice: str = "",
    create_group_name: str = "",
    create_group_web_search_query: str = "",
    create_group_is_default: bool = False,
) -> HTMLResponse:
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)

    context = _admin_groups_context(
        request,
        timeline_filters,
        notice=_notice_message(notice),
        create_group_name=create_group_name,
        create_group_web_search_query=create_group_web_search_query,
        create_group_is_default=create_group_is_default,
    )
    return templates.TemplateResponse(request, "admin_groups.html", context)


@app.post("/admin/groups", response_model=None)
async def create_group_route(request: Request) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    raw_name = str(form.get("name", ""))
    raw_web_search_query = str(form.get("web_search_query", ""))
    is_default = form.get("is_default") is not None
    normalized_name = normalize_timeline_group_name(raw_name)
    normalized_query_value = _normalize_group_form_value(raw_web_search_query)

    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        try:
            create_timeline_group(
                connection,
                raw_name,
                raw_web_search_query,
                is_default=is_default,
            )
        except TimelineGroupValidationError as exc:
            return templates.TemplateResponse(
                request,
                "admin_groups.html",
                _admin_groups_context(
                    request,
                    timeline_filters,
                    create_group_name=normalized_name,
                    create_group_web_search_query=normalized_query_value,
                    create_group_is_default=is_default,
                    create_group_errors={exc.field: str(exc)},
                ),
                status_code=400,
            )

    return RedirectResponse(url="/admin/groups?notice=created", status_code=303)


@app.post("/admin/groups/{group_id:int}", response_model=None)
async def rename_group_route(
    request: Request, group_id: int
) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    raw_name = str(form.get("name", ""))
    raw_web_search_query = str(form.get("web_search_query", ""))
    is_default = form.get("is_default") is not None

    with connection_context() as connection:
        existing_group = get_timeline_group(connection, group_id)
        timeline_filters = list_timeline_groups(connection)
        try:
            rename_timeline_group(
                connection,
                group_id,
                raw_name,
                raw_web_search_query,
                is_default=is_default,
            )
        except TimelineGroupValidationError as exc:
            return templates.TemplateResponse(
                request,
                "admin_groups.html",
                _admin_groups_context(
                    request,
                    timeline_filters,
                    edit_group_errors={group_id: {exc.field: str(exc)}},
                    edit_group_values={
                        group_id: {
                            "name": normalize_timeline_group_name(raw_name),
                            "web_search_query": _normalize_group_form_value(
                                raw_web_search_query
                            ),
                            "is_default": is_default,
                        }
                    },
                ),
                status_code=400,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    updated_query = _normalize_group_form_value(raw_web_search_query)
    previous_query = existing_group.web_search_query if existing_group else None
    if previous_query != updated_query:
        clear_group_web_search_cache(previous_query)
        clear_group_web_search_cache(updated_query)

    return RedirectResponse(url="/admin/groups?notice=updated", status_code=303)


@app.post("/admin/groups/{group_id:int}/delete", response_model=None)
async def delete_group_route(
    request: Request, group_id: int
) -> RedirectResponse | HTMLResponse:
    with connection_context() as connection:
        timeline_filters = list_timeline_groups(connection)
        try:
            delete_timeline_group(connection, group_id)
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "admin_groups.html",
                _admin_groups_context(
                    request,
                    timeline_filters,
                    delete_group_errors={group_id: str(exc)},
                ),
                status_code=400,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RedirectResponse(url="/admin/groups?notice=deleted", status_code=303)


@app.post("/entries/generate")
async def generate_entry_preview(
    request: Request,
    title: str = Form(""),
    source_url: str = Form(""),
    generated_text: str = Form(""),
) -> HTMLResponse:
    prompt_title = title.strip()
    cleaned_source_url = source_url.strip()
    if not prompt_title and not cleaned_source_url:
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            {
                "generated_text": generated_text,
                "feedback_message": "Title or source URL is required to generate a summary.",
                "feedback_class": "text-danger",
            },
            status_code=400,
        )

    extraction = None
    extraction_error = None
    if cleaned_source_url:
        extraction = await extract_url_text(cleaned_source_url)
        if extraction is None:
            extraction_error = (
                "Source extraction failed. Summary generation used title-only mode."
            )
            if not prompt_title:
                return templates.TemplateResponse(
                    request,
                    "partials/generated_preview.html",
                    {
                        "generated_text": generated_text,
                        "feedback_message": (
                            "Source extraction failed. Enter a title or try a different URL."
                        ),
                        "feedback_class": "text-danger",
                    },
                    status_code=400,
                )

    try:
        suggestion = await generate_entry_suggestion(prompt_title, extraction)
        generated_text = suggestion.draft_html
    except DraftGenerationConfigurationError as exc:
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            {
                "generated_text": generated_text,
                "feedback_message": str(exc),
                "feedback_class": "text-danger",
            },
            status_code=400,
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            {
                "generated_text": generated_text,
                "feedback_message": str(exc),
                "feedback_class": "text-danger",
            },
            status_code=400,
        )
    except DraftGenerationError as exc:
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            {
                "generated_text": generated_text,
                "feedback_message": str(exc),
                "feedback_class": "text-danger",
            },
            status_code=502,
        )
    except Exception:
        logging.getLogger(__name__).exception("Draft generation failed")
        return templates.TemplateResponse(
            request,
            "partials/generated_preview.html",
            {
                "generated_text": generated_text,
                "feedback_message": (
                    "Summary generation failed. You can still write manually."
                ),
                "feedback_class": "text-danger",
            },
            status_code=500,
        )

    return templates.TemplateResponse(
        request,
        "partials/generated_preview.html",
        {
            "generated_text": generated_text,
            "suggested_title": suggestion.title,
            "suggested_event_year": ""
            if suggestion.event_year is None
            else str(suggestion.event_year),
            "suggested_event_month": ""
            if suggestion.event_month is None
            else str(suggestion.event_month),
            "suggested_event_day": ""
            if suggestion.event_day is None
            else str(suggestion.event_day),
            "feedback_message": (
                extraction_error
                or (
                    "Summary, title, and date suggestions generated with source context."
                    if extraction is not None
                    else "Summary, title, and date suggestions generated from the current input."
                )
            ),
            "feedback_class": "text-warning" if extraction_error else "text-success",
        },
    )


@app.post("/entries/preview-html")
async def preview_entry_html(
    request: Request,
    raw_html: str = Form(""),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/html_preview_content.html",
        {
            "preview_html": sanitize_rich_text(raw_html),
            "empty_message": "Rendered preview updates here as you type.",
        },
    )


@app.get("/dev/extract")
async def dev_extract(source_url: str) -> JSONResponse:
    extraction = await extract_url_text(source_url)
    if extraction is None:
        return JSONResponse(
            {"ok": False, "message": "Extraction failed."}, status_code=400
        )
    return JSONResponse(
        {
            "ok": True,
            "title": extraction.title,
            "preview": extraction.text[:500],
        }
    )


def _parse_group_id(raw_group_id: str) -> int | None:
    normalized = raw_group_id.strip()
    if not normalized:
        return None
    if normalized.lower() == "all":
        return None
    try:
        value = int(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Timeline group not found") from exc
    if value <= 0:
        raise HTTPException(status_code=404, detail="Timeline group not found")
    return value


def _parse_timeline_cursor(cursor: str) -> tuple[int, str, int] | None:
    normalized_cursor = cursor.strip()
    if not normalized_cursor:
        return None
    try:
        return decode_timeline_cursor(normalized_cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid timeline cursor") from exc


def _parse_search_cursor(cursor: str) -> int | None:
    normalized_cursor = cursor.strip()
    if not normalized_cursor:
        return None
    try:
        return decode_search_cursor(normalized_cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid search cursor") from exc


def _load_group_scope(connection: Any, *, q: str, group_id: str) -> dict[str, Any]:
    normalized_query = q.strip()
    normalized_group_id = group_id.strip().lower()
    explicit_all_groups = normalized_group_id == "all"
    selected_group_id = _parse_group_id(group_id)
    timeline_filters = list_timeline_groups(connection)
    selected_group = None
    if explicit_all_groups:
        selected_group_id = None
    elif selected_group_id is not None:
        selected_group = get_timeline_group(connection, selected_group_id)
        if selected_group is None:
            raise HTTPException(status_code=404, detail="Timeline group not found")
    else:
        selected_group = get_default_timeline_group(connection)
        if selected_group is not None:
            selected_group_id = selected_group.id

    return {
        "normalized_query": normalized_query,
        "selected_group_id": selected_group_id,
        "selected_group_query_value": (
            "all"
            if explicit_all_groups
            else (str(selected_group_id) if selected_group_id is not None else "")
        ),
        "timeline_filters": timeline_filters,
        "selected_group": selected_group,
        "explicit_all_groups": explicit_all_groups,
        "scope_key": _build_timeline_scope_key(selected_group_id, normalized_query),
    }


def _load_timeline_scope(connection: Any, *, q: str, group_id: str) -> dict[str, Any]:
    scope = _load_group_scope(connection, q=q, group_id=group_id)

    match_count = None
    if scope["normalized_query"]:
        match_count = len(
            filter_timeline_entries(
                connection,
                scope["normalized_query"],
                group_id=scope["selected_group_id"],
            )
        )

    scope["match_count"] = match_count
    scope["timeline_web_search"] = {
        "show_panel": bool(
            scope["selected_group"] and scope["selected_group"].web_search_query
        ),
        "provider_is_copilot": _is_copilot_provider(),
    }
    return scope


def _build_timeline_scope_key(group_id: int | None, query: str) -> str:
    return json.dumps(
        {"group_id": group_id, "query": query},
        sort_keys=True,
        separators=(",", ":"),
    )


def _encode_sse_event(event_name: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), default=str)
    return f"event: {event_name}\ndata: {body}\n\n"


def _list_entries_for_scope(
    connection: Any,
    *,
    normalized_query: str,
    selected_group_id: int | None,
):
    if normalized_query:
        return filter_timeline_entries(
            connection,
            normalized_query,
            group_id=selected_group_id,
        )
    return list_timeline_entries(connection, group_id=selected_group_id)


def _list_timeline_details_for_scope(
    connection: Any,
    *,
    normalized_query: str,
    selected_group_id: int | None,
    page_size: int | None,
    cursor: tuple[int, str, int] | None = None,
):
    if normalized_query:
        scoped_entries = filter_timeline_entries(
            connection,
            normalized_query,
            group_id=selected_group_id,
        )
        return paginate_entries_in_memory(
            scoped_entries,
            page_size=page_size,
            cursor=cursor,
        )

    return list_timeline_entries_page(
        connection,
        group_id=selected_group_id,
        page_size=page_size,
        cursor=cursor,
    )


def _render_partial(template_name: str, **context: Any) -> str:
    template = templates.get_template(template_name)
    return template.render(**context)


def _notice_message(notice: str) -> str | None:
    if notice == "created":
        return "Timeline group created."
    if notice == "updated":
        return "Timeline group updated."
    if notice == "deleted":
        return "Timeline group deleted."
    return None


def _admin_groups_context(
    request: Request,
    timeline_filters: list,
    *,
    notice: str | None = None,
    create_group_name: str = "",
    create_group_web_search_query: str = "",
    create_group_is_default: bool = False,
    create_group_errors: dict[str, str] | None = None,
    edit_group_errors: dict[int, dict[str, str]] | None = None,
    edit_group_values: dict[int, dict[str, object]] | None = None,
    delete_group_errors: dict[int, str] | None = None,
) -> dict[str, object]:
    return {
        "request": request,
        "page_title": "Admin",
        "timeline_filters": timeline_filters,
        "selected_group_id": None,
        "selected_group_query_value": "",
        "query": "",
        "notice": notice,
        "create_group_name": create_group_name,
        "create_group_web_search_query": create_group_web_search_query,
        "create_group_is_default": create_group_is_default,
        "create_group_errors": create_group_errors or {},
        "edit_group_errors": edit_group_errors or {},
        "edit_group_values": edit_group_values or {},
        "delete_group_errors": delete_group_errors or {},
    }


def _normalize_group_form_value(value: str) -> str:
    return " ".join(value.strip().split())


def _is_copilot_provider() -> bool:
    try:
        return load_ai_provider() == "copilot"
    except DraftGenerationConfigurationError:
        return False
