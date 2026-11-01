"""The real popup callback must never resolve uncertainty without consent."""
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from messages_panel import MessagesPanel


class RecoveryUiTests(unittest.TestCase):
    def panel(self,kind='confirm',selected='job',value=None):
        panel=SimpleNamespace(recovery_results=queue.Queue(),recovery_running=True,
            recovery_button=Mock(),status=Mock(),selected_job=selected,render_jobs=Mock())
        panel.recovery_results.put((kind,{'id':'job','version':'version','title':'Test','repeat':True},
            [{'token':'old-token'}] if value is None else value))
        return panel

    def test_cancel_or_switched_selection_never_applies(self):
        for selected in ('job','another-job'):
            panel=self.panel(selected=selected)
            with patch('messages_panel.messagebox.askyesno',return_value=False), \
                 patch('delivery_recovery.apply_resolution') as apply:
                MessagesPanel.poll_recovery(panel)
            apply.assert_not_called()
            self.assertFalse(panel.recovery_running)

    def test_confirm_passes_only_snapshot_tokens_and_exact_version(self):
        panel=self.panel()
        def immediate(*,target,daemon):
            return SimpleNamespace(start=target)
        with patch('messages_panel.messagebox.askyesno',return_value=True) as dialog, \
             patch('messages_panel.threading.Thread',side_effect=immediate), \
             patch('delivery_recovery.apply_resolution',return_value={'action':'future_only'}) as apply:
            MessagesPanel.poll_recovery(panel)
        self.assertEqual(('job','version',['old-token']),apply.call_args.args[1:4])
        self.assertEqual('no',dialog.call_args.kwargs['default'])
        self.assertTrue(panel.recovery_running)
        MessagesPanel.poll_recovery(panel)
        panel.render_jobs.assert_called_once()
        self.assertIn('senza conferma',panel.status.set.call_args.args[0])

    def test_expired_snapshot_shows_error_without_claiming_success(self):
        panel=self.panel(kind='error',value='Le prenotazioni sono cambiate')
        with patch('messages_panel.messagebox.askyesno') as dialog:
            MessagesPanel.poll_recovery(panel)
        dialog.assert_not_called();panel.render_jobs.assert_not_called()
        self.assertIn('Nessuna modifica',panel.status.set.call_args.args[0])


if __name__=='__main__':unittest.main()
