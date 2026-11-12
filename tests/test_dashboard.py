from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py"
SECTIONS = ["Live accuracy", "Open PRs: stuck risk", "Repo health", "Model info"]


@pytest.mark.parametrize("section", SECTIONS)
def test_dashboard_sections_render_without_exception(section):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception
    at.sidebar.radio[0].set_value(section).run()
    assert not at.exception, [e.value for e in at.exception]
