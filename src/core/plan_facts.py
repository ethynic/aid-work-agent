"""Attempt-local business-plan facts; projections never become read authority."""

from copy import deepcopy
from threading import RLock


class PlanFacts:
    def __init__(self):
        self._plans = {}
        self._lock = RLock()

    def load(self, session_id):
        with self._lock:
            return deepcopy(self._plans.get(session_id))

    def save(self, session_id, data):
        with self._lock:
            self._plans[session_id] = deepcopy(data)

    def delete(self, session_id):
        with self._lock:
            self._plans.pop(session_id, None)
