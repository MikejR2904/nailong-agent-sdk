from pathlib import Path

SUPPORT_ROOT = Path(__file__).resolve().parent
TESTS_ROOT = SUPPORT_ROOT.parent
REPO_ROOT = TESTS_ROOT.parent
SRC_ROOT = REPO_ROOT / "src"
STUB_ROOT = SUPPORT_ROOT / "stubs"
