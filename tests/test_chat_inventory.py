import unittest
from dataclasses import dataclass
from unittest.mock import patch
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
    def test_current_codex_chat_actions_replace_archive_without_losing_project_or_status(self):
        for action in ('Azioni della chat','Chat actions'):
            with self.subTest(action=action):
                controls=[Node('ListControl'),Node('ListItemControl','Project'),Node('ButtonControl','Project'),
                          Node('ButtonControl','Azioni del progetto per Project'),
                          Node('ListControl','Attività programmate in Project')]
                updated=row('Chat recente','In elaborazione')
                updated[-1]=Node('ButtonControl',action)
                controls+=updated+[Node('ListControl')]+updated
                result=entries(controls,'codex')
                self.assertEqual(len(result),1)
                self.assertEqual(result[0][0]['title'],'Chat recente')
                self.assertEqual(result[0][0]['group'],'Attività programmate in Project')
                self.assertEqual(result[0][0]['status'],'In elaborazione')
                self.assertFalse(result[0][0]['ambiguous'])

    def test_claude_pull_request_badges_preserve_exact_title_and_project(self):
        for suffix in (' Aperta · 8 pull request',' Open · 8 pull requests',' Chiuso · 2 pull request',
                       ' In esecuzione Bozza · 2 pull request'):
            controls=[Node('TextControl','project','heading'),Node('ButtonControl','project'),
                      Node('ButtonControl','Regia'+suffix),Node('ButtonControl','Altre opzioni per Regia'),
                      Node('ButtonControl','Regia figlia Avviata da Claude da Regia'),
                      Node('ButtonControl','Altre opzioni per Regia figlia')]
            with patch('chat_desktop.walk',return_value=[]):rows=entries(controls,'claude')
            matched=timer_target(rows,{'app':'claude','title':'Regia','group':'project'})
            self.assertEqual(len(matched),1)
            self.assertEqual(matched[0][1].Name,'Regia'+suffix)
            self.assertFalse(matched[0][0]['ambiguous'])
            self.assertEqual(timer_target(rows,{'app':'claude','title':'Regia','group':'other'}),[])

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
