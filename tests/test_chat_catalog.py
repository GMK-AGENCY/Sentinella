import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from chat_catalog import claude_header, claude_metadata, codex_metadata, enrich, filter_chats, project_name


class CatalogTests(unittest.TestCase):
    def test_only_project_name_is_displayed(self):
        self.assertEqual(project_name("Attività programmate in Siti Web AI"), "Siti Web AI")
        self.assertEqual(project_name(r"C:\Users\Test\Work\project"), "project")
        self.assertEqual(project_name("Utente (locale)"), "Senza progetto")

    def test_search_and_all_filters_compose(self):
        rows = [
            {"app": "codex", "title": "Project Regia", "project": "Herd", "status": "In elaborazione", "last_activity_at": 999},
            {"app": "claude", "title": "Project Regia", "project": "Altro", "status": "Disponibile", "last_activity_at": 999},
        ]
        filtered = filter_chats(rows, "Project herd", "Codex", "Herd", "In elaborazione", now=1000)
        self.assertEqual(filtered, rows[:1])
        self.assertEqual(filter_chats(rows, "non presente", now=1000), [])

    def test_seven_day_boundary_and_unknown_dates_are_explicit(self):
        now = 1000000
        rows = [{"app": "codex", "title": label, "last_activity_at": date} for label, date in (("recent", now), ("boundary", now - 7 * 86400), ("old", now - 7 * 86400 - 1), ("unknown", None))]
        self.assertEqual([r["title"] for r in filter_chats(rows, now=now)], ["recent", "boundary"])
        self.assertEqual(len(filter_chats(rows, days=0, now=now)), 4)

    def test_metadata_does_not_change_native_destination_identity(self):
        native = {"app": "codex", "title": "Chat", "group": "Attività programmate in Mix", "selector": "Chat"}
        with patch("chat_catalog.codex_metadata", return_value=[{"title": "Chat", "project": "Mix", "last_activity_at": 500}]):
            rows, warning = enrich([native], "codex")
        self.assertEqual(rows[0]["group"], "Attività programmate in Mix")
        self.assertEqual(rows[0]["selector"], "Chat")
        self.assertEqual(rows[0]["project"], "Mix")
        self.assertIsNone(warning)

    def test_ambiguous_metadata_does_not_invent_an_activity_date(self):
        row = {"title": "Identico", "group": "Recenti"}
        metadata = [{"title": "Identico", "project": name, "last_activity_at": 999} for name in ("A", "B")]
        with patch("chat_catalog.codex_metadata", return_value=metadata):
            rows, _ = enrich([row], "codex")
        self.assertIsNone(rows[0]["last_activity_at"])
        self.assertIsNone(rows[0].get('session_id'))
        self.assertEqual(filter_chats(rows,query='11111111',days=0),[])

    def test_session_id_can_be_searched_only_when_metadata_is_unique(self):
        row={'app':'codex','title':'Test chat','group':'Project'}
        metadata=[{'title':'Test chat','project':'Project','thread_id':'11111111-1111-4111-8111-111111111111','last_activity_at':999}]
        with patch('chat_catalog.codex_metadata',return_value=metadata):
            rows,_=enrich([row],'codex')
        self.assertEqual(rows[0]['session_id'],metadata[0]['thread_id'])
        self.assertEqual(filter_chats(rows,query='11111111',days=0),rows)
        with patch('chat_catalog.codex_metadata',return_value=[metadata[0],{**metadata[0],'thread_id':'22222222-2222-4222-8222-222222222222'}]):
            rows,_=enrich([row],'codex')
        self.assertIsNone(rows[0].get('session_id'))

    def test_claude_reads_bounded_metadata_prefix_and_ignores_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sessions = root / "claude-code-sessions" / "account" / "workspace"
            sessions.mkdir(parents=True)
            meta = {"cwd": r"C:\work\Project", "lastActivityAt": 1792833120000, "isArchived": False, "title": "Test"}
            path = sessions / "local_test.json"
            path.write_text("  " + json.dumps(meta)[:-1] + ', "large_configuration": "' + "x" * 300000 + '"}', encoding="utf-8")
            self.assertEqual(claude_header(path)["title"], "Test")
            result = claude_metadata({"Test"}, [root])
            self.assertEqual(result[0]["project"], "Project")
            self.assertEqual(result[0]["last_activity_at"], 1792833120)
            meta["isArchived"] = True
            path.write_text(json.dumps(meta), encoding="utf-8")
            self.assertEqual(claude_metadata({"Test"}, [root]), [])

    def test_codex_metadata_matches_custom_title_and_reads_only_open_chats(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state_5.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript("CREATE TABLE projects (id TEXT, name TEXT); CREATE TABLE threads (id TEXT,name TEXT,title TEXT,cwd TEXT,updated_at_ms INTEGER,updated_at INTEGER,recency_at_ms INTEGER,recency_at INTEGER,project_id TEXT,archived INTEGER);")
            connection.execute("INSERT INTO projects VALUES ('p','Mix')")
            values = ("thread-1", "Custom", "Initial prompt", "C:/work/Mix", 1792833120000, 1792833120, None, None, "p", 0)
            connection.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)", values)
            connection.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)", (*values[:-1], 1))
            connection.commit()
            connection.close()
            result = codex_metadata({"Custom"}, path)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0], {"thread_id": "thread-1", "title": "Custom", "project": "Mix", "last_activity_at": 1792833120})


if __name__ == "__main__":
    unittest.main()
