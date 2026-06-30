from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Utils.parse_version import parse_version_text

raw = "- Build Version: 15.0.4430.1\n- Cumulative Update (CU): CU32"
result = parse_version_text(raw)
print("Result:", result)

