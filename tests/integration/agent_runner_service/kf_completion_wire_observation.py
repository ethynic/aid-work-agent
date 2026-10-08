"""Transparent test observation of an actual native domain wire owner.

This is not an observer implementation that grants dispatch or builds proofs.
The supplied original owner performs every before_post/known/unknown/unwritten
operation; original return values/exceptions pass through. Optional methods are
inherited through __getattr__, so absent optional production ports stay absent.
"""
import asyncio


class ObserveOriginalWireOwner:
    def __init__(self, original, *, hold_result=None, result_arrived=None):
        self._original = original
        self._hold_result = hold_result
        self._result_arrived = result_arrived
        self.events = []
        # Exact parsed tuples are private test memory for explicit redelivery,
        # never fabricated text, a public proof, output or a postcrash claim.
        self._actual_results = []

    def __getattr__(self, name):
        return getattr(self._original, name)

    async def before_post(self, path, descriptor):
        decision = await self._original.before_post(path, descriptor)
        self.events.append({'stage': 'original_before_post_returned',
            'dispatch': decision.dispatch, 'has_replay': decision.replay_result is not None})
        return decision

    async def known(self, operation, result):
        self._actual_results.append((operation, result))
        self.events.append({'stage': 'original_parsed_result_received',
            'response_origin': result.get('response_origin'), 'errcode': result.get('errcode')})
        if self._result_arrived is not None:
            self._result_arrived.set()
        if self._hold_result is not None:
            assert await asyncio.to_thread(self._hold_result.wait, 20), 'OWN_WIRE_RESULT_RETURN_GATE_TIMEOUT'
        returned = await self._original.known(operation, result)
        self.events.append({'stage': 'original_known_returned'})
        return returned

    async def unknown(self, operation, code):
        returned = await self._original.unknown(operation, code)
        self.events.append({'stage': 'original_unknown_returned', 'code': code})
        return returned

    async def unwritten(self, operation, code):
        returned = await self._original.unwritten(operation, code)
        self.events.append({'stage': 'original_unwritten_returned', 'code': code})
        return returned
