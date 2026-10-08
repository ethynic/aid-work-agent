"""Explicit bounded event retention maintenance; it never advances execution."""

import argparse
import asyncio

from src.db.database import get_db_connection
from .event_contracts import EventContractError, nonnegative
from .event_repository import EventRepository
from .public_view import decoded


def prune_batch(*, connection_factory=get_db_connection, after_queue_order=0,
                keep_last=1000, roots=16, delete_limit=1000):
    nonnegative(after_queue_order,'queue_cursor')
    nonnegative(keep_last,'retained_count')
    if type(roots) is not int or not 1 <= roots <= 32:
        raise EventContractError('INVALID_EVENT_MAINTENANCE_LIMIT')
    if type(delete_limit) is not int or not 1 <= delete_limit <= 1000:
        raise EventContractError('INVALID_EVENT_RETENTION_LIMIT')
    with connection_factory() as connection:
        cursor = connection.cursor()
        cursor.execute('''SELECT runner_id,queue_order FROM agent_runners WHERE queue_order>%s
            AND event_seq-event_floor_seq>%s ORDER BY queue_order LIMIT %s''',
            (after_queue_order,keep_last,roots))
        candidates = cursor.fetchall()
    deleted = 0
    for candidate in candidates:
        with connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE SKIP LOCKED',
                           (candidate['runner_id'],))
            row = decoded(cursor.fetchone())
            if row is None:
                continue
            _, count = EventRepository.prune_in_tx(cursor,row,max(0,row['event_seq']-keep_last),
                                                   limit=delete_limit)
            deleted += count
            connection.commit()
    # A subsequent invocation can loop to zero and revisit roots needing more
    # than one deletion batch. No head is reconstructed from retained rows.
    return {'deleted':deleted,'next_cursor':candidates[-1]['queue_order'] if len(candidates)==roots else 0}


async def _run(arguments):
    from src.db.database import init_postgres_pool, close_postgres_pool
    await asyncio.to_thread(init_postgres_pool)
    try:
        value = await asyncio.to_thread(prune_batch,after_queue_order=arguments.after,
            keep_last=arguments.keep_last,roots=arguments.roots,delete_limit=arguments.delete_limit)
        print('events_deleted={deleted} next_cursor={next_cursor}'.format(**value))
    finally:
        await asyncio.to_thread(close_postgres_pool)


def main():
    parser = argparse.ArgumentParser(description='Prune one bounded AgentRunner public event prefix batch')
    parser.add_argument('--after',type=int,default=0)
    parser.add_argument('--keep-last',type=int,default=1000)
    parser.add_argument('--roots',type=int,default=16)
    parser.add_argument('--delete-limit',type=int,default=1000)
    asyncio.run(_run(parser.parse_args()))


if __name__ == '__main__':
    main()
