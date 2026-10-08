"""Fresh-process external resource safety; not worker execution acceptance."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.integration


def test_actual_resolved_model_endpoints_and_trace_connection_are_isolated(
        provider_peer, service_processes, service_database):
    # Keep resource resolution in a fresh Python process so pytest module caches,
    # monkeypatches and its initialized pools cannot make the probe pass.
    script = """
import json
import runpy
import sys
verify = runpy.run_path(sys.argv[1])["verify_resource_targets"]
proof = verify(database_name=sys.argv[2], provider_port=int(sys.argv[3]))
print("RESOURCE_PROOF:" + json.dumps(proof))
"""
    safety = str(Path(__file__).with_name("safety.py"))
    port = str(urlsplit(provider_peer.environment["QWEN_BASE_URL"]).port)
    index = len(service_processes.children)
    child = service_processes.start(["-c", script, safety, service_database.name, port],
                                     environment=provider_peer.environment,
                                     private_working_directory=True)
    try:
        assert child.wait(timeout=15) == 0, "Fresh-process resource verification failed (private log retained until teardown)"
        lines = (service_processes.root / f"child-{index}.log").read_text().splitlines()
        proofs = [json.loads(line.removeprefix("RESOURCE_PROOF:")) for line in lines
                  if line.startswith("RESOURCE_PROOF:")]
        assert proofs == [{"database": service_database.name, "trace_connection_verified": True,
                           "effective_provider_endpoints_local": True}]
        # The safety probe only connects to the logs DB for a read-only query;
        # it must not dispatch even one provider call.
        assert not provider_peer.errors
    finally:
        service_processes.stop(child)
