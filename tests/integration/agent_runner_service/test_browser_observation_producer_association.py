"""Minimal prior producer regressions with current real sidecar configuration.

Calls the unchanged original assertions; these are associated cases, not new
observation coverage or a re-run of all prior native/GC/storage proofs.
"""
import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import observation_configuration
from .test_worker import workers, api_pair, prices
from .test_worker_browser_producer import test_actual_browser_start_live_original_fence_and_confirmed_close as original_completed
from .test_worker_browser_park import test_actual_wait_is_committed_before_suspension_and_browser_lease_survives_park as original_parked

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_associated_original_default_cli_completed_browser_with_observer_listener_ready(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    workers.environment.update(observation_configuration['worker'])
    original_completed(workers,actors,service_database,browser_page,browser_redis)


def test_associated_original_resident_park_lease_and_shutdown_with_observer_listener_ready(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    workers.environment.update(observation_configuration['worker'])
    original_parked(workers,actors,service_database,browser_page,browser_redis)
