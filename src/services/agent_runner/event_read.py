"""Short authorized pages on a newly borrowed, exclusively owned connection.

The connection factory must open its own scope, never lend a business cursor.
"""

from .contracts import RunnerError
from .event_repository import EventRepository
from .public_view import decoded


class RunnerEventRead:
    def __init__(self, repository, authorizer):
        self.repository, self.authorizer = repository, authorizer

    def page(self, runner_id, credentials, after_seq, *, limit=100, max_bytes=65_536):
        self.authorizer.verify_service(credentials.get('service_id'),credentials.get('service_token'),
                                       credentials.get('actor_source'))
        token = credentials.get('user_token')
        # Original verification may use Redis. It never runs with an event-read
        # connection held; the authoritative token row is rechecked below.
        user_id = self.authorizer.token_verifier(token,auto_refresh=False) if token else None
        if token and not user_id or not token and not credentials.get('actor_source'):
            raise RunnerError('USER_UNAUTHORIZED',401)
        with self.repository.connection_factory() as connection:
            # The default pool checkout has executed SELECT 1. End only that
            # health-probe transaction on this freshly borrowed read connection
            # before establishing the authoritative snapshot. No business work
            # has run in this scope, and no caller cursor is accepted here.
            connection.rollback()
            cursor = connection.cursor()
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            # Observer reads use a local budget, never change the shared pool.
            cursor.execute("SET LOCAL statement_timeout = '2s'")
            cursor.execute("SET LOCAL lock_timeout = '1s'")
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(runner_id,))
            row = decoded(cursor.fetchone())
            if row is None:
                raise RunnerError('RUNNER_NOT_FOUND',404)
            self.authorizer.authorize_read_in_tx(cursor,row,credentials,verified_user_id=user_id)
            return EventRepository.read_page_in_tx(cursor,row,after_seq,limit=limit,max_bytes=max_bytes)
