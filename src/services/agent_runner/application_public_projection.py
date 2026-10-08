"""This application's finite cursor-only public projection composition.

Generic persistence calls this neutral entry point. Tool-owned scope and facts
remain in the installed domain projection; no execution or permission changes.
"""

from .browser_card_projection import project_snapshot, project_terminal_result, sync_browser_display


def project_public_snapshot(cursor, row, *, checkpoint=None, snapshot=None, status=None):
    return project_snapshot(cursor,row,checkpoint=checkpoint,snapshot=snapshot,status=status)


def sync_public_display(cursor, row, *, advance_view=True):
    return sync_browser_display(cursor,row,advance_view=advance_view)


def project_terminal_public_state(cursor, row, result):
    snapshot = project_snapshot(cursor, row, status=result['status'])
    return snapshot, project_terminal_result(result, snapshot)
