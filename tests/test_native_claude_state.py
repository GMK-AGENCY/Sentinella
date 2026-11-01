import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid

from native_claude_state import claude_chat_state
from native_transports import require_idle,ChatBusy,NotDispatched


class ClaudeOwnerStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.owner=Mock();self.owner.pid=123
        self.runtime=str(uuid.uuid4());self.logical=str(uuid.uuid4())
        self.owner.create_time.return_value=1000.;self.owner.is_running.return_value=True
        self.owner.cmdline.return_value=['claude','--resume='+self.runtime]
        self.path=self.directory/'123.json'
        self.metadata={'pid':123,'procStart':str(int((1000+11644473600)*10000000)),
            'sessionId':self.runtime,'hostSessionId':'local_'+self.logical,
            'version':'2.1.308','kind':'interactive','entrypoint':'claude-desktop',
            'status':'idle','updatedAt':1001000,'statusUpdatedAt':1001000}
        self.write()
    def tearDown(self):self.tmp.cleanup()
    def write(self):self.path.write_text(json.dumps(self.metadata),encoding='utf-8')
    def read(self):return claude_chat_state(self.owner,runtime_id=self.runtime,logical_id=self.logical,session_dir=self.directory,now=1002)
    def test_exact_owner_idle_and_busy(self):
        self.assertEqual('idle',self.read().state)
        self.metadata['status']='busy';self.write()
        with self.assertRaises(ChatBusy):require_idle(self.read())
    def test_rename_never_changes_identity_or_idle_result(self):
        self.metadata['name']='Completely renamed';self.write()
        self.assertEqual('idle',self.read().state)
    def test_wrong_process_session_version_or_unrecognized_state_is_unknown(self):
        for key,value in [('pid',124),('procStart','1'),('sessionId',str(uuid.uuid4())),
            ('hostSessionId','local_'+str(uuid.uuid4())),('version','2.1.999'),('status','waiting'),('updatedAt',999000)]:
            with self.subTest(key=key):
                original=self.metadata[key];self.metadata[key]=value;self.write()
                self.assertEqual('unknown',self.read().state)
                self.metadata[key]=original;self.write()
    def test_disappeared_process_or_changed_resume_never_means_idle(self):
        self.owner.is_running.return_value=False
        self.assertEqual('unknown',self.read().state)
        self.owner.is_running.return_value=True;self.owner.cmdline.return_value=['claude','--resume='+str(uuid.uuid4())]
        self.assertEqual('unknown',self.read().state)
    def test_missing_truncated_or_oversized_metadata_never_means_idle(self):
        for body in ('{','x'*40000):
            self.path.write_text(body)
            self.assertEqual('unknown',self.read().state)
        self.path.unlink();self.assertEqual('unknown',self.read().state)
    def test_unknown_requires_explanation_and_blocks_before_effect(self):
        self.metadata['status']='new-state';self.write()
        with self.assertRaises(NotDispatched):require_idle(self.read())


if __name__=='__main__':unittest.main()
