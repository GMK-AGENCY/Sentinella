import unittest
from dataclasses import dataclass
from chat_desktop import entries, timer_target
from question_desktop import chat_content_available


@dataclass
class Node:
    ControlTypeName: str
    Name: str = ""
    AriaRole: str = ""


def row(title, status=""):
    return [Node("ListItemControl", title), Node("ButtonControl", title), Node("TextControl", status, "status"), Node("ButtonControl", "Fissa chat"), Node("ButtonControl", "Archivia chat")]


class InventoryTests(unittest.TestCase):
    def test_window_title_buttons_are_not_available_chat_content(self):
        self.assertFalse(chat_content_available([Node("ButtonControl", "Riduci a icona"), Node("ButtonControl", "Ingrandisci")]))
        self.assertTrue(chat_content_available([Node("DocumentControl", "Una chat")]))

    def test_projects_and_recent_duplicates_keep_one_scoped_target(self):
        controls = [Node("ButtonControl", "Home"), Node("ListControl")]
        controls += row("Project Regia", "In elaborazione")
        controls += [Node("ListControl", "Attività programmate in herd")]
        controls += row("Lavoro")
        controls += [Node("ListControl")]
        controls += row("Lavoro")
        controls += [Node("ButtonControl", "Copia messaggio")]
        result = [r for r, _ in entries(controls, "codex")]
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Project Regia")
        self.assertEqual(result[0]["status"], "In elaborazione")
        self.assertEqual(result[1]["group"], "Attività programmate in herd")
        self.assertFalse(result[1]["ambiguous"])

    def test_identical_titles_in_same_list_are_not_sendable(self):
        controls = [Node("ListControl")] + row("Duplicata") + row("Duplicata")
        result = entries(controls, "codex")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0][0]["ambiguous"])

    def test_unique_timer_target_survives_pinning(self):
        job = {"app": "codex", "title": "Project Regia", "group": "Recenti"}
        rows = [({"title": "Project Regia", "group": "Fissati", "ambiguous": False}, object())]
        self.assertEqual(timer_target(rows, job), rows)

    def test_pinning_fallback_rejects_duplicate_titles_and_project_changes(self):
        job = {"app": "codex", "title": "Regia", "group": "Recenti"}
        pinned = ({"title": "Regia", "group": "Fissati", "ambiguous": False}, object())
        project = ({"title": "Regia", "group": "Progetto diverso", "ambiguous": False}, object())
        self.assertEqual(timer_target([pinned, project], job), [])
        self.assertEqual(timer_target([project], job), [])
        pinned[0]["ambiguous"] = True
        self.assertEqual(timer_target([pinned], job), [])
