"""The actual legacy Web application behind the old already-opened pages.

The old page calls ``POST /api/chat/stream`` and ``POST /api/chat/{sid}/cancel``
on the real application; the transparent bridge being accepted is the one wired
into ``src.main``, not a re-implemented test double. This factory only returns
that app so a subprocess gateway can serve it with the isolated fixtures.
"""


def create_app():
    from src.main import app

    return app
