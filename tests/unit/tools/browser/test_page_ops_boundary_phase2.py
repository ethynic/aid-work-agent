import inspect

from src.tools.browser.page_ops import PageOps


def test_page_ops_does_not_import_or_hold_legacy_browser_objects():
    source = inspect.getsource(__import__("src.tools.browser.page_ops", fromlist=["PageOps"]))
    assert "from src.tools.browser.session" not in source
    assert "self.session" not in source
    assert "self.page" not in source
    assert list(inspect.signature(PageOps).parameters) == ["executor", "run_id", "initial_seq"]
