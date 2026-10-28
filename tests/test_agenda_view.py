import copy
from datetime import date, datetime
import unittest

from agenda_view import schedule_sections


class AgendaSectionsTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 28)
        self.now = datetime(2026, 10, 28, 12).astimezone().timestamp()

    def row(self, key, status='Attivo', when=None):
        return {'job_id':key, 'title':'Same title', 'status':status, 'when':self.now if when is None else when}

    def test_same_job_moves_between_sections_without_duplication_or_mutation(self):
        record = self.row('one')
        before = copy.deepcopy(record)
        for status, attention, expected in [('Attivo', {}, 'planned'), ('Attivo', {'one':{}}, 'waiting'),
                                             ('In pausa', {'one':{}}, 'paused'), ('Completato', {}, 'completed')]:
            record = {**before, 'status':status}
            sections = schedule_sections([record], attention, self.today)
            self.assertEqual(len(sections), 1)
            self.assertEqual(sections[0][3], expected)
            self.assertEqual(sections[0][2], [record])
        self.assertEqual(before, self.row('one'))

    def test_ten_duplicate_titles_keep_exact_ids_and_waiting_paused_separate(self):
        rows = [self.row(str(i), 'In pausa' if i>=8 else 'Attivo') for i in range(10)]
        sections = schedule_sections(rows, {'3':{}, '4':{}, '8':{}}, self.today)
        self.assertEqual([(s[3], len(s[2])) for s in sections], [('planned',6), ('waiting',2), ('paused',2)])
        flattened = [row['job_id'] for section in sections for row in section[2]]
        self.assertEqual(len(flattened), 10)
        self.assertEqual(set(flattened), {str(i) for i in range(10)})

    def test_future_dates_are_labeled_separately_from_today(self):
        tomorrow = datetime(2026,10,29,0,1).astimezone().timestamp()
        later = datetime(2026,11,4,12).astimezone().timestamp()
        rows = [self.row('later',when=later),self.row('today'),self.row('tomorrow',when=tomorrow)]
        before = copy.deepcopy(rows)
        sections = schedule_sections(rows, {}, self.today)
        self.assertEqual([section[1] for section in sections], ['Oggi · 28/10','Domani · 29/10','04/11/2026'])
        self.assertEqual(rows, before)
