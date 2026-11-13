"""scripts/check.sh runs scripts/i18n_lint.py before pytest and stops at its first finding, so text a reader sees that isn't marked for
translation (or an allowlisted line in ruqqus/translations/lint_allow.txt that no longer matches its source exactly) would stop the whole
check while pytest and CI stayed green. Run here too, so pytest catches that drift."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_interface_text_is_left_unmarked_and_every_allowlisted_line_still_matches():
    done = subprocess.run([sys.executable, "scripts/i18n_lint.py"], cwd=ROOT, capture_output=True, text=True,
                          env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}, timeout=120)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert "unmarked: 0" in done.stdout + done.stderr
