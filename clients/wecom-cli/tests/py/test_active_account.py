"""当前主窗口进程归属：真实 Windows 句柄接口 + 合成文件，不访问用户数据。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'drivers' / 'py'))
import wecom_localdb as drv


class ActiveAccountTests(unittest.TestCase):
    def test_only_unique_local_message_db_is_evidence(self):
        one = r'C:\data\111\Data\message.db'
        self.assertEqual(drv._account_from_message_paths(42, [one, one.upper()])['message_db'].lower(), one.lower())
        self.assertEqual(drv._account_from_message_paths(42, [one, r'C:\data\222\Data\message.db'])['error'], 'ACCOUNT_AMBIGUOUS')
        self.assertEqual(drv._account_from_message_paths(42, [r'\\server\111\Data\message.db', r'C:\data\111\Data\user.db'])['error'], 'ACCOUNT_UNAVAILABLE')
        self.assertFalse(drv._account_from_message_paths(42, [one, r'\\server\222\Data\message.db'])['ok'])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows 句柄 API')
    def test_same_pid_live_handles_follow_account_change_and_reject_ambiguity(self):
        """名字相同的合成进程只开空文件；同 PID 释放/切换后不得沿用旧证据。"""
        with tempfile.TemporaryDirectory(prefix='wecom-account-test-') as temp:
            base = Path(temp)
            source = base / 'hold.cs'
            helper = base / 'WXWork.exe'
            source.write_text('''using System; using System.IO; using System.Collections.Generic;
class Hold { static void Main() {
  var handles = new List<FileStream>();
  try { string line; while ((line = Console.ReadLine()) != null) {
    foreach (var h in handles) h.Dispose(); handles.Clear();
    if (line.Length > 0) foreach (var p in line.Split('|'))
      handles.Add(File.Open(p, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete));
    Console.WriteLine("READY");
  } } finally { foreach (var h in handles) h.Dispose(); }
} }''', encoding='utf-8')
            quote = lambda p: "'" + str(p).replace("'", "''") + "'"
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                            'Add-Type -Path ' + quote(source) + ' -OutputAssembly ' + quote(helper)
                            + ' -OutputType ConsoleApplication'], check=True, capture_output=True, timeout=30)
            paths = []
            for account in ['111', '222']:
                path = base / account / 'Data' / 'message.db'
                path.parent.mkdir(parents=True)
                path.touch()
                paths.append(str(path))
            with subprocess.Popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) as child:
                try:
                    for opened, expected in [([paths[0]], paths[0]), (paths, 'ACCOUNT_AMBIGUOUS'),
                                             ([paths[1]], paths[1]), ([], 'ACCOUNT_UNAVAILABLE')]:
                        child.stdin.write('|'.join(opened) + '\n')
                        child.stdin.flush()
                        self.assertEqual(child.stdout.readline().strip(), 'READY')
                        payload = drv.cmd_active_account(child.pid)
                        if expected.endswith('message.db'):
                            self.assertTrue(payload['ok'], payload)
                            self.assertEqual(os.path.normcase(payload['message_db']), os.path.normcase(expected))
                        else:
                            self.assertEqual(payload['error'], expected)
                    # CLI 协议也必须拒绝非 WXWork 映像，不能只靠调用方传 PID。
                    proc = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'drivers' / 'py' / 'wecom_localdb.py'),
                                           'active-account', str(os.getpid())], capture_output=True, text=True, timeout=10)
                    payload = json.loads(proc.stdout.split('LOCALDB_JSON:')[-1])
                    self.assertEqual(payload['error'], 'PROCESS_MISMATCH')
                finally:
                    child.stdin.close()
                    child.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
