"""Installed 3.15 discovery, selective lazy imports, and eager/frozen operation."""

import json
import os
import subprocess
import sys
import unittest
from typing import Any


class TestImports(unittest.TestCase):
    def child(self, code: str, *, frozen: str = "on") -> Any:
        env = {key: value for key, value in os.environ.items() if key != "PYTHON_LAZY_IMPORTS"}
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", "-X", f"frozen_modules={frozen}", "-c", code],
            env=env,
            check=False,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_import_and_discovery_leave_unrelated_handlers_unloaded(self) -> None:
        result = self.child(
            "import json,sys; import research_repo_tools.cli as c; "
            "assert 'research_repo_tools.config' not in sys.modules; "
            "p=c.parser(); a=p.parse_args(['python','check']); "
            "assert a.group=='python' and a.action=='check'; "
            "print(json.dumps(sorted(sys.modules)))"
        )
        for name in (
            "research_repo_tools.config",
            "research_repo_tools.measurement",
            "research_repo_tools.release_assets",
            "research_repo_tools.publication",
            "nbclient",
            "nbformat",
            "pypdf",
        ):
            self.assertNotIn(name, result)

    def test_eager_loading_and_frozen_module_modes_preserve_command_discovery(self) -> None:
        code = (
            "import json,types; import research_repo_tools.cli as c; "
            # Frozen application bundlers can force explicitly imported names;
            # no alternate implementation or global lazy mode is needed.
            "assert isinstance(c.config,types.ModuleType); assert callable(c._write_stdout); "
            "p=c.parser(); a=p.parse_args(['performance','measure','plan.toml','--allow-git-mutations','--payload','a','--manifest','b']); "
            "print(json.dumps(dict(group=a.group,action=a.action)))"
        )
        self.assertEqual(self.child(code, frozen="on"), self.child(code, frozen="off"))

    def test_handler_optional_dependency_failure_keeps_normal_cli_diagnostic(self) -> None:
        result = self.child(
            "import contextlib,io,json,tempfile; from unittest.mock import patch; from pathlib import Path; "
            "from research_repo_tools.cli import main; "
            "d=tempfile.TemporaryDirectory(); p=Path(d.name)/'paper.pdf'; p.write_bytes(b'%PDF'); "
            "s=io.StringIO(); "
            "\nwith patch.dict('sys.modules',{'pypdf':None}),contextlib.redirect_stderr(s):\n"
            " status=main(['papers','check',str(p)])\n"
            "print(json.dumps(dict(status=status,error=s.getvalue())))"
        )
        self.assertEqual(result["status"], 1)
        self.assertIn("research-repo-tools:", result["error"])
        self.assertIn("research-repo-tools[papers]", result["error"])
        self.assertIn("install the optional extra", result["error"])
        self.assertNotIn("Traceback", result["error"])

    def test_lazy_type_imports_preserve_annotation_introspection(self) -> None:
        result = self.child(
            "import json; from typing import get_type_hints; "
            "from research_repo_tools import changelog,cli,performance,performance_workflows; "
            "from research_repo_tools.config import Config; "
            "functions=((changelog.generate,'config'),(cli.run,'settings'),(performance.run,'settings'),(performance_workflows.run,'settings')); "
            "print(json.dumps([get_type_hints(function)[parameter] is Config for function,parameter in functions]))"
        )
        self.assertEqual(result, [True, True, True, True])


if __name__ == "__main__":
    unittest.main()
