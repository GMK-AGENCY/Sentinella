"""Read-only chat metadata and local list filters; never expands action scope."""
from datetime import datetime
import json
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import time


def project_name(value):
    value = (value or "").strip()
    for prefix in ("Attività programmate in ", "Scheduled tasks in ", "Tasks in "):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if not value or value in {"Recenti", "Recents", "Sessioni", "Sessions", "Fissati", "Pinned", "Oggi", "Ieri", "Today", "Yesterday"} or value.endswith(" (locale)"):
        return "Senza progetto"
    return PureWindowsPath(value).name if "\\" in value or "/" in value else value


def epoch(value):
    try:
        value = float(value)
        return value / 1000 if value > 100000000000 else value
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (AttributeError, ValueError, TypeError):
            return None


def codex_metadata(titles, path=None, thread_ids=(), *, all_chats=False):
    if path is None:
        candidates = list(Path(os.environ.get('CODEX_HOME',Path.home()/'.codex')).glob("state_[0-9]*.sqlite"))
        path = max(candidates, key=lambda p: int(p.stem.split("_")[-1])) if candidates else None
    if not path or not (titles or thread_ids or all_chats):
        if all_chats and not path:
            raise ValueError('Indice locale Codex non disponibile')
        return []
    connection = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=0.2)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
        if not {'id','title','archived'} <= columns:
            raise ValueError('Formato dell’indice Codex non compatibile: aggiorna Sentinella')
        name = "COALESCE(NULLIF(t.name,''),t.title)" if 'name' in columns else 't.title'
        has_projects = ('project_id' in columns and
                        {'id','name'} <= {row[1] for row in connection.execute('PRAGMA table_info(projects)')})
        project = 'p.name' if has_projects else 'NULL'
        join = 'LEFT JOIN projects p ON p.id=t.project_id' if has_projects else ''
        optional = ','.join('t.'+key if key in columns else 'NULL AS '+key
                            for key in ('cwd','updated_at_ms','updated_at','recency_at_ms','recency_at'))
        slots = ",".join("?" for _ in titles)
        clauses=[];args=[]
        if titles:
            clauses.append(f'({name} IN ({slots}) OR t.title IN ({slots}))');args += [*titles,*titles]
        if thread_ids:
            clauses.append('t.id IN ('+','.join('?' for _ in thread_ids)+')');args += list(thread_ids)
        where = '1' if all_chats else ' OR '.join(clauses)
        source = 't.source' if 'source' in columns else 'NULL'
        sql = f"""SELECT t.id AS thread_id, {name} AS title, {optional}, {project} AS project, {source} AS source
                  FROM threads t {join}
                  WHERE t.archived=0 AND ({where})"""
        result = []
        for row in connection.execute(sql, args):
            if all_chats:
                if not row['title'] or not row['title'].strip():
                    continue
                try:
                    origin = json.loads(row['source'] or 'null')
                except (ValueError, TypeError):
                    origin = None
                if isinstance(origin,dict) and 'subagent' in origin:
                    continue
            dates = [epoch(row[key]) for key in ("updated_at_ms", "updated_at", "recency_at_ms", "recency_at")]
            result.append({"thread_id": row["thread_id"], "title": row["title"], "project": project_name(row["project"] or row["cwd"]), "last_activity_at": max((n for n in dates if n is not None), default=None)})
        return result
    finally:
        connection.close()


def codex_inventory(native_rows=(), native_error=None):
    """List by stable local IDs, independently of sidebar labels/virtualization.

    Local storage is read-only and is not an invocation surface. Native actions
    must still resolve and revalidate their recipient separately.
    """
    warning = native_error
    try:
        metadata = codex_metadata(set(), all_chats=True)
    except (OSError, ValueError, sqlite3.Error) as exc:
        rows, enrichment_warning = enrich(list(native_rows), 'codex')
        for row in rows:
            if not row.get('thread_id'):
                row.update(ambiguous=True,status='ID non verificato')
        warning = f'Indice Codex non leggibile: {exc}. Elenco limitato alle chat riconosciute nell’app.'
        if not rows:
            raise ValueError(warning) from exc
        return rows, warning
    native_rows, enrichment_warning = enrich(list(native_rows), 'codex')
    observed = {row.get('thread_id'): row for row in native_rows if row.get('thread_id')}
    rows = []
    from collections import Counter
    counts = Counter(item['title'] for item in metadata)
    for item in metadata:
        native = observed.get(item['thread_id'], {})
        rows.append({'app':'codex', 'group':native.get('group',item['project']),
                     'selector':item['title'], 'status':native.get('status','Stato non rilevato'),
                     'ambiguous':counts[item['title']]>1, **item, 'session_id':item['thread_id']})
    # The unified app also shows ChatGPT conversations. They are not local
    # Codex threads: never fabricate an ID or offer them as Codex recipients.
    if metadata and not native_rows and not warning:
        warning = 'Chat lette dall’indice locale. Interfaccia Codex non riconosciuta: verifica il controllo di domande e consensi. Gli invii usano il collegamento locale, verificato separatamente.'
    return sorted(rows,key=lambda row:row.get('last_activity_at') or 0,reverse=True), warning or enrichment_warning


def claude_header(path):
    # Session files also contain large tool configuration. Only parse the first
    # 16 KiB and stop after the top-level metadata, never reading chat histories.
    with path.open("r", encoding="utf-8") as stream:
        prefix = stream.read(16384)
    decoder = json.JSONDecoder()
    position = prefix.index("{") + 1 if prefix.lstrip().startswith("{") else len(prefix)
    fields = {}
    wanted = {"title", "cwd", "originCwd", "lastActivityAt", "createdAt", "isArchived"}
    while position < len(prefix):
        try:
            while position < len(prefix) and prefix[position] in " \r\n\t,":
                position += 1
            key, position = decoder.raw_decode(prefix, position)
            while prefix[position].isspace():
                position += 1
            if prefix[position] != ":":
                break
            position += 1
            while prefix[position].isspace():
                position += 1
            value, position = decoder.raw_decode(prefix, position)
            if key in wanted:
                fields[key] = value
            if {"title", "cwd", "lastActivityAt", "isArchived"} <= fields.keys():
                break
        except (ValueError, IndexError, TypeError):
            break
    return fields


def claude_metadata(titles, roots=None, session_ids=()):
    if roots is None:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        roots = list((local / "Claude-Data").glob("claude_*"))
        roots.append(Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Claude")
    result = []
    seen = set()
    for root in roots:
        for path in (Path(root) / "claude-code-sessions").glob("*/*/*.json"):
            if path in seen:
                continue
            seen.add(path)
            if len(seen) > 3000:
                return result
            try:
                row = claude_header(path)
                sid=path.stem.removeprefix('local_')
                if (row.get("title") not in titles and sid not in session_ids) or row.get("isArchived"):
                    continue
                result.append({"title": row["title"], "session_id": path.stem.removeprefix('local_'), "project": project_name(row.get("originCwd") or row.get("cwd")), "last_activity_at": epoch(row.get("lastActivityAt") or row.get("createdAt"))})
            except (OSError, ValueError):
                continue
    return result


def enrich(rows, app):
    titles = {row["title"] for row in rows}
    warning = None
    try:
        metadata = codex_metadata(titles) if app == "codex" else claude_metadata(titles)
    except (OSError, ValueError, sqlite3.Error) as exc:
        metadata = []
        warning = f"Date {app} non disponibili: {type(exc).__name__}"
    for row in rows:
        display = project_name(row.get("group"))
        matches = [item for item in metadata if item["title"] == row["title"]]
        exact = [item for item in matches if item["project"].casefold() == display.casefold()]
        matches = exact or matches
        if len(matches) == 1:
            row.update(matches[0])
            row['session_id']=matches[0].get('session_id') or matches[0].get('thread_id')
            if app == "codex" and display != "Senza progetto":
                # The sidebar knows the user's project label, which may group
                # several checkouts with different folder names.
                row["project"] = display
        else:
            row.update(project=display, last_activity_at=None)
            row.pop('session_id',None)
            row.pop('thread_id',None)
    return rows, warning


def state_name(row):
    if row.get("ambiguous"):
        return "Nome duplicato"
    status = row.get("status", "Disponibile")
    lowered = status.casefold()
    if any(word in lowered for word in ("in elaborazione", "in esecuzione", "running", "working")):
        return "In elaborazione"
    if any(word in lowered for word in ("input", "domanda", "bloccata", "bloccato", "attention")):
        return "Richiede input"
    return status


def filter_chats(rows, query="", app="Tutte", project="Tutti", state="Tutti", days=7, now=None):
    now = time.time() if now is None else now
    words = query.casefold().split()
    result = []
    for row in rows:
        display = row.get("project") or project_name(row.get("group"))
        if app != "Tutte" and row["app"].casefold() != app.casefold():
            continue
        if project != "Tutti" and display != project:
            continue
        if state != "Tutti" and state_name(row) != state:
            continue
        if not all(word in f"{row['title']} {display} {row.get('session_id') or row.get('thread_id') or ''}".casefold() for word in words):
            continue
        date = row.get("last_activity_at")
        if days and (date is None or date < now - days * 86400):
            continue
        result.append(row)
    return sorted(result, key=lambda row: (row.get("last_activity_at") or 0), reverse=True)
