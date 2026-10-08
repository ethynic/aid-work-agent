"""Test process composition for the real Browser Web routes.

Use the ordinary gateway's PostgreSQL lifespan and tenant middleware. Only the
router mount is added; auth, tickets, sidecar HTTP/WS and Browser ownership are
production implementations. No main scheduler or channel lifecycle is started.
"""


def create_app():
    from src.api.agent_runner_web import create_app as create_gateway
    from src.api.browser_runs import router

    app = create_gateway()
    app.include_router(router)
    return app
