"""The launch adapter must not change the driver's input or termination contract."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PythonBootstrapTests(unittest.TestCase):
    def test_driver_keeps_argv_stdin_exit_code_and_lazy_crypto(self):
        bootstrap = Path(__file__).resolve().parents[2] / 'drivers/py/python_bootstrap.py'
        with tempfile.TemporaryDirectory(prefix='wecom-bootstrap-contract-') as directory:
            script = Path(directory) / 'driver.py'
            script.write_text(
                'import json,sys\n'
                'data=sys.stdin.read()\n'
                'print(json.dumps({"argv":sys.argv,"input_length":len(data),'
                '"crypto_loaded":"Crypto" in sys.modules}))\n'
                'sys.exit(7)\n', encoding='utf-8')
            result = subprocess.run([sys.executable, '-I', '-B', str(bootstrap), str(script),
                '含空格 value', '--flag'], input='synthetic-private-input',
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), {'argv':[str(script),'含空格 value','--flag'],
                'input_length':len('synthetic-private-input'), 'crypto_loaded':False})
            self.assertNotIn('synthetic-private-input', result.stdout)


if __name__ == '__main__':
    unittest.main()
