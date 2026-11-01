import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from native_process import OwnedJsonProcess,ProtocolError


class OwnedClientTests(unittest.TestCase):
    def test_nonreading_child_cannot_block_large_write_or_close(self):
        started=time.monotonic()
        with tempfile.TemporaryDirectory() as folder:
            client=OwnedJsonProcess([sys.executable,'-c','import time;time.sleep(20)'],folder)
            try:
                with self.assertRaises(TimeoutError):client.send({'payload':'x'*2000000})
            finally:client.close()
            self.assertIsNotNone(client.process.poll())
        self.assertLess(time.monotonic()-started,6)

    def test_interactive_request_fails_without_approving_it(self):
        code="import json,time;print(json.dumps({'id':9,'method':'approval/request','params':{}}),flush=True);time.sleep(10)"
        with tempfile.TemporaryDirectory() as folder,OwnedJsonProcess([sys.executable,'-c',code],folder) as client:
            with self.assertRaisesRegex(ProtocolError,'nessun consenso automatico'):
                client.receive_until(lambda value:False,1)


if __name__=='__main__':unittest.main()
