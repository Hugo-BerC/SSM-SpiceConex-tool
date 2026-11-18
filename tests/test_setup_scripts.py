from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class InstallerScriptTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Native PowerShell validation requires Windows")
    def test_powershell_parses_and_run_quiet_preserves_native_arguments(self):
        def literal(value): return "'" + str(value).replace("'", "''") + "'"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "folder with spaces"; folder.mkdir()
            probe, output, script = folder / "argv probe.py", folder / "result.json", folder / "test.ps1"
            probe.write_text("import json,sys;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]),encoding='utf-8')", encoding="utf-8")
            arguments = ["", "two words", 'quote"value', "trailing\\", "O'Brien", "Espa\u00f1a"]
            text = f"""$ErrorActionPreference='Stop'
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile({literal(ROOT / 'setup.ps1')},[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) {{ throw ($parseErrors | Out-String) }}
$functions=$ast.FindAll({{param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('ConvertTo-NativeArgument','Run-Quiet')}},$true)
foreach ($function in $functions) {{ Invoke-Expression $function.Extent.Text }}
Run-Quiet 'benign argv probe' {literal(sys.executable)} @({','.join(literal(value) for value in [str(probe),str(output),*arguments])})
"""
            script.write_text(text, encoding="utf-8-sig")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(arguments, json.loads(output.read_text(encoding="utf-8")))

    def test_shell_installer_syntax(self):
        shell = shutil.which("sh")
        if not shell and os.name == "nt":
            candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/bin/bash.exe"
            shell = str(candidate) if candidate.is_file() else None
        if not shell: self.skipTest("POSIX shell is unavailable")
        result = subprocess.run([shell, "-n", str(ROOT / "setup.sh")], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
