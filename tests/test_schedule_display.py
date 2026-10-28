import unittest
from datetime import datetime
from schedule_display import schedule_display


class DisplayTests(unittest.TestCase):
    def test_prepared_message_keeps_the_actual_reason_for_waiting(self):
        state={'version':'new','phase':'drafted','due':self.now,
               'detail':"Cambio chat rinviato durante l'uso del PC"}
        display=schedule_display(self.job,state,{},self.now)
        self.assertEqual(display['status'],'Attende cambio chat')
        self.assertEqual(display['next_precise'],'Apri la chat destinataria')
        self.assertIn('mentre lavori',display['detail'])
        state.update(phase='uncertain',local_stage='drafted')
        self.assertEqual(schedule_display(self.job,state,{},self.now)['status'],'Attende cambio chat')

    def test_known_queue_is_not_reclassified_as_unknown_after_two_minutes(self):
        state = {'version':'new', 'phase':'uncertain', 'local_stage':'awaiting_receipt',
                 'reserved_at':self.now-3600, 'due':self.now-3500,
                 'detail':'Messaggio accodato alla chat: attendo che venga elaborato'}
        display = schedule_display(self.job,state,{},self.now)
        self.assertEqual(display['status'], 'Accodato alla chat')
        self.assertEqual(display['status_key'], 'queued')
        self.assertEqual(display['severity'], 'info')

    def test_resume_does_not_claim_an_uncertain_attempt_was_received(self):
        state = {'version':'new', 'phase':'uncertain', 'local_stage':'drafted', 'detail':'Bozza scomparsa'}
        paused = schedule_display({**self.job,'enabled':False},state,{},self.now)
        active = schedule_display(self.job,state,{},self.now)
        self.assertEqual(paused['status'], 'In pausa')
        self.assertEqual(active['status'], 'Ricezione non confermata')
        self.assertEqual(active['next_precise'], 'Controlla il registro')
    def test_pause_overrides_even_uncertain_and_draft_states(self):
        for stage in ('reserved', 'drafted', 'sending', 'awaiting_receipt'):
            state = {'version': 'new', 'phase': 'uncertain', 'local_stage': stage, 'due': self.now, 'detail': 'Bozza presente'}
            for job, config in (({**self.job, 'enabled': False}, {}), (self.job, {'paused': True})):
                display = schedule_display(job, state, config, self.now)
                self.assertEqual(display['activation'], 'In pausa')
                self.assertEqual(display['next_precise'], 'Nessun nuovo invio')

    def test_countdown_separates_active_from_waiting(self):
        state = {'version': 'new', 'due': self.now + 754}
        display = schedule_display(self.job, state, {}, self.now)
        self.assertEqual(display['activation'], 'Attivo')
        self.assertEqual(display['next_precise'], 'Tra 12 min 34 s')
        state['due'] = self.now - 32
        self.assertEqual(schedule_display(self.job, state, {}, self.now)['next_precise'], 'Appena possibile')
    def test_reserved_native_send_waiting_for_user_draft_is_not_uncertain_delivery(self):
        job = {"id": "x", "version": "v", "enabled": True, "interval_seconds": 1800}
        state = {"version": "v", "phase": "uncertain", "due": 100, "local_stage": "reserved", "detail": "Bozza presente: non viene sovrascritta né inviata"}
        result = schedule_display(job, state, {}, 200)
        self.assertEqual(result["status"], "Bozza da completare")
        self.assertEqual(result["next"], "Bozza da completare")

    def test_recent_native_send_is_in_verification_then_requires_attention(self):
        job = {"id": "x", "version": "v", "enabled": True, "interval_seconds": 1800}
        state = {"version": "v", "phase": "uncertain", "due": 100, "local_stage": "awaiting_receipt", "reserved_at": 190}
        self.assertEqual(schedule_display(job, state, {}, 200)["status"], "Conferma in corso")
        self.assertEqual(schedule_display(job, state, {}, 400)["status"], "Ricezione non confermata")

    def setUp(self):
        self.now = datetime(2026, 10, 24, 13, 0).timestamp()
        self.job = {"id": "test", "version": "new", "enabled": True, "activated_at": self.now, "interval_seconds": 900, "repeat": True}

    def test_prior_version_never_displays_old_due_or_last_sent(self):
        stale = {"version": "old", "due": self.now - 7200, "last_sent": self.now - 900, "phase": "drafted"}
        display = schedule_display(self.job, stale, {}, self.now)
        self.assertEqual(display["next"], "Tra 15 min · 13:15")
        self.assertEqual(display["last_sent"], "Mai inviato")

    def test_overdue_is_waiting_instead_of_an_old_clock_time(self):
        state = {"version": "new", "due": self.now - 60}
        display = schedule_display(self.job, state, {}, self.now)
        self.assertEqual(display["next"], "Appena possibile")
        self.assertEqual(display["status"], "In attesa")

    def test_overdue_on_active_pc_explains_why_it_waits(self):
        state = {"version": "new", "due": self.now - 60}
        display = schedule_display(self.job, state, {}, self.now, "PC in uso: attendo 30 secondi")
        self.assertEqual(display["next"], "Attende PC libero")

    def test_tomorrow_and_pause_are_unambiguous(self):
        state = {"version": "new", "due": self.now + 86400}
        self.assertIn("25/10", schedule_display(self.job, state, {}, self.now)["next"])
        self.assertEqual(schedule_display(self.job, state, {"paused": True}, self.now)["next"], "Riprendi Sentinella")

    def test_uncertain_send_never_looks_scheduled(self):
        state = {"version": "new", "due": self.now + 900, "phase": "uncertain"}
        self.assertEqual(schedule_display(self.job, state, {}, self.now)["status"], "Ricezione non confermata")

    def test_busy_retry_is_not_presented_as_a_scheduled_send(self):
        state = {"version": "new", "due": self.now + 30, "detail": "Chat in elaborazione: invio rinviato senza cambiare chat"}
        display = schedule_display(self.job, state, {}, self.now)
        self.assertEqual(display["next"], "Quando è libera")
        self.assertEqual(display["status"], "In attesa")
        self.assertEqual(display['next_precise'], 'Quando è libera')

    def test_future_retry_clock_does_not_hide_existing_draft(self):
        state = {'version':'new', 'due':self.now+30, 'detail':'Bozza presente: conservata'}
        self.assertEqual(schedule_display(self.job,state,{},self.now)['next_precise'], 'Completa la bozza nella chat')
