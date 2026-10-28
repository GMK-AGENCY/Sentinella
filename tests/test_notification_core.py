import unittest
from notification_core import Notification, Deduplicator, classify, is_target, after_cleanup


class DetectionTests(unittest.TestCase):
    def test_cleanup_cutoff_survives_restart_without_hiding_new_toasts(self):
        from datetime import datetime
        cutoff=datetime.fromisoformat('2026-10-28T12:00:00+00:00').timestamp()
        values=dict(app='Claude',app_id='claude',notification_id=1,title='Chat',body='Notifica')
        for created,expected in [('2026-10-28T11:59:59Z',False),('2026-10-28T14:00:00+02:00',False),
                                 ('2026-10-28T12:00:01Z',True),('invalid-date',True)]:
            with self.subTest(created=created):
                self.assertEqual(after_cleanup(Notification(**values,created=created),cutoff),expected)
        self.assertTrue(after_cleanup(Notification(**values,created='2026-10-28T11:00:00Z'),0))

    def test_windows_sender_codex_despite_chatgpt_label(self):
        self.assertTrue(is_target("ChatGPT", "OpenAI.Codex_2p2nqsd0c76g0!App"))

    def test_claude_sender(self):
        self.assertTrue(is_target("Claude", "com.anthropic.claude"))

    def test_other_senders_not_selected_by_body(self):
        self.assertFalse(is_target("Outlook", "Microsoft.Outlook"))
        self.assertFalse(is_target("Codexium", "com.example.Codexium"))

    def test_italian_and_english_approval_text(self):
        for text in ("Consenti una volta", "Consenti sempre", "Consenti 1 volta", "Allow once", "Claude needs your approval", "Permission required", "Richiesta di autorizzazione"):
            with self.subTest(text=text):
                self.assertEqual(classify("Prova", text), "Possibile approvazione")

    def test_generic_question_is_not_claimed_as_permission_prompt(self):
        self.assertEqual(classify("Prova Sentinella", "Il tuo assistente ha una domanda"), "Richiede attenzione")

    def test_reported_block_is_only_attention_signal(self):
        self.assertEqual(classify("Chat", "My è bloccata sul commit da un’autorizzazione dell’app"), "Richiede attenzione")

    def test_deduplicates_snapshot_but_detects_updated_body(self):
        values = dict(app="Codex", app_id="OpenAI.Codex!App", notification_id=1, created="2026-10-24T06:00:00Z", title="Prova", body="Allow once")
        first = Notification(**values)
        cache = Deduplicator()
        self.assertTrue(cache.add(first))
        self.assertFalse(cache.add(first))
        values["body"] = "Finished"
        self.assertTrue(cache.add(Notification(**values)))

    def test_dedup_cache_is_bounded(self):
        cache = Deduplicator(capacity=2)
        for number in range(3):
            self.assertTrue(cache.add(Notification("Codex", "id", number, "date", "title", "body")))
        self.assertEqual(len(cache.seen), 2)


if __name__ == "__main__":
    unittest.main()
