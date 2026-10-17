"""One archive scope for every Discord-facing reader, including trivia."""
import sqlite3

from bot.llm.tools.discord_ctx import get_current_discord_context


def allowed_channels(ctx=None):
    ctx = ctx if ctx is not None else get_current_discord_context()
    guild, destination, actor = (ctx.get(k) for k in ("guild", "channel", "author"))
    if not all((guild, destination, actor)):
        return set()
    result = set()
    channels = list(getattr(guild, "text_channels", ())) + list(getattr(guild, "threads", ()))
    if destination not in channels:
        channels.append(destination)
    for channel in channels:
        try:
            perm = channel.permissions_for(actor)
            if not (perm.view_channel and perm.read_message_history):
                continue
            if channel.id == destination.id:
                result.add(int(channel.id))
                continue
            # Cross-channel public replies may use public text channels only.
            # Private threads and role-restricted sources stay in their channel.
            if channel not in getattr(guild, "text_channels", ()):
                continue
            public = channel.permissions_for(guild.default_role)
            if public.view_channel and public.read_message_history:
                result.add(int(channel.id))
        except (AttributeError, TypeError):
            continue
    return result


def scope_connection(connection, ctx=None):
    ctx = ctx if ctx is not None else get_current_discord_context()
    guild = getattr(ctx.get("guild"), "id", 0)
    channels = ",".join(str(value) for value in sorted(allowed_channels(ctx))) or "-1"
    # TEMP view shadows unqualified legacy SQL, including every fallback query.
    connection.execute(f"CREATE TEMP VIEW messages AS SELECT * FROM main.messages WHERE guild={int(guild)} AND channel IN ({channels})")
    # Old dossiers have no source-channel provenance. Rebuild from scoped rows.
    for table in ("user_profiles", "user_profile_links"):
        if connection.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            connection.execute(f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} WHERE 0")
    return connection


def open_archive(path, timeout=5, ctx=None):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
    connection.row_factory = sqlite3.Row
    try:
        return scope_connection(connection, ctx)
    except BaseException:
        connection.close()
        raise
