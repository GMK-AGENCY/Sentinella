import unittest
from types import SimpleNamespace as Node
from question_desktop import plain_claude_candidate


def heading(name):
    return Node(Name=name, AriaRole="heading", ControlTypeName="TextControl")


class PlainClaudeTests(unittest.TestCase):
    def test_only_latest_unanswered_message_and_scoped_chat_can_be_considered(self):
        nodes = [heading("Claude ha risposto: Prima domanda?"), heading("Hai detto: Già risposto")]
        self.assertFalse(plain_claude_candidate(nodes, "Test", ["Test"]))
        nodes += [heading("Claude ha risposto: Nuova domanda?")]
        self.assertTrue(plain_claude_candidate(nodes, "Test", ["Test"]))
        self.assertFalse(plain_claude_candidate(nodes, "Other", ["Test"]))
        self.assertFalse(plain_claude_candidate(nodes, "", []))

    def test_running_assistant_is_not_an_unanswered_final_question(self):
        nodes = [heading("Claude ha risposto: Sto pensando?")]
        nodes += [Node(Name="Interrompi", AriaRole="", ControlTypeName="ButtonControl")]
        self.assertFalse(plain_claude_candidate(nodes, "Test", []))

    def test_acknowledgement_quoting_a_deferred_question_does_not_repeat_the_reply(self):
        nodes = [heading("Hai detto: utente è fuori."), heading("Claude ha risposto: Conservo la domanda: breve o esteso?")]
        self.assertFalse(plain_claude_candidate(nodes,"Test",[],"utente è fuori. Conserva la domanda."))
        nodes += [heading("Hai detto: Sono rientrato"),heading("Claude ha risposto: Procedo?")]
        self.assertTrue(plain_claude_candidate(nodes,"Test",[],"utente è fuori. Conserva la domanda."))
