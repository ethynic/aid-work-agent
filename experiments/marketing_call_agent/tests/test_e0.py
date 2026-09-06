"""全部为合成 PNG / mock，不是设备实验通过证据。"""
import io
import os
import json
import subprocess
from unittest.mock import Mock

import httpx
import pytest
from PIL import Image

from experiments.marketing_call_agent.cli import Device, ProbeError, main
from experiments.marketing_call_agent.observe import map_box, prepare_image, validate_location, locate, observe


def response(**changes):
    data = dict(frame_id="frame", page="设置", found=True, bbox_px=[10,20,30,40], label="搜索", ambiguity=False)
    return json.dumps(data | changes)


@pytest.mark.parametrize("changes", [dict(frame_id="old"), dict(bbox_px=[-1,0,20,20]), dict(bbox_px=[0,0,101,20]), dict(bbox_px=[20,20,10,10]), dict(bbox_px=[1,2,3]), dict(bbox_px=[True,2,3,4]), dict(ambiguity=True), dict(found=False), dict(found=False,bbox_px=None),dict(found="true"),dict(extra="injection")])
def test_reject(changes):
    with pytest.raises(ProbeError):
        validate_location(response(**changes), "frame", (100,200))


def test_geometry():
    assert map_box([10,20,30,40],(100,200),(1000,2000)) == [100,200,300,400]
    assert validate_location(response(),"frame",(100,200)).label == "搜索"


def png(color="white", size=(1080,2400)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_resize():
    original, scaled = prepare_image(png())
    assert original.size == (1080,2400)
    assert scaled.size == (576,1280)


@pytest.mark.parametrize("data", [b"invalid",png("black"),png(size=(200,100))])
def test_bad_screens(data):
    with pytest.raises(ProbeError):
        prepare_image(data)


def test_adb_parameter_array(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/tools/adb")
    run = Mock(return_value=Mock(stdout=b"List of devices attached\nabc;echo_secret\tdevice\n"))
    monkeypatch.setattr(subprocess,"run",run)
    device = Device("adb","abc;echo_secret")
    device.check()
    device.screenshot()
    assert run.call_args.args[0] == ["/tools/adb","-s","abc;echo_secret","exec-out","screencap","-p"]
    assert "shell" not in run.call_args.kwargs


def test_adb_timeout_sanitized(monkeypatch):
    monkeypatch.setattr("shutil.which",lambda _: "/tools/adb")
    monkeypatch.setattr(subprocess,"run",Mock(side_effect=subprocess.TimeoutExpired("secret",15)))
    with pytest.raises(ProbeError) as exc:
        Device("adb","test").check()
    assert "secret" not in str(exc.value)


def test_doctor_missing(monkeypatch,capsys):
    monkeypatch.setattr("shutil.which",lambda _:None)
    assert main(["doctor"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"


def test_observe_requires_confirmation(capsys):
    assert main(["observe","--target","搜索","--output","/tmp/unused"]) == 2
    assert "TEST_CONTENT" in capsys.readouterr().out


def test_http_payload_and_error(monkeypatch):
    captured = {}
    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200,json={"choices":[{"message":{"content":response()}}]})
    original_client = httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kwargs:original_client(transport=httpx.MockTransport(handler),**kwargs))
    assert locate(Image.new("RGB",(100,200)),"搜索","frame","https://open.bigmodel.cn/api/paas/v4/chat/completions","secret").found
    assert captured["model"] == "glm-5.3-flash"
    assert captured["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_offline_pipeline_not_pass(monkeypatch,tmp_path):
    monkeypatch.setenv("MCA_VISION_ENDPOINT","https://open.bigmodel.cn/api/paas/v4/chat/completions")
    monkeypatch.setenv("MCA_VISION_API_KEY","fictional")
    monkeypatch.setattr("experiments.marketing_call_agent.observe.locate",lambda im,target,frame,*_:validate_location(response(frame_id=frame),frame,im.size))
    result = observe(Mock(screenshot=lambda:png()),"搜索",tmp_path/"evidence")
    assert result["status"] == "BLOCKED"
    assert (tmp_path/"evidence"/"overlay.png").is_file()
    if os.name != "nt":
        assert (tmp_path/"evidence").stat().st_mode & 0o777 == 0o700
        assert (tmp_path/"evidence"/"result.json").stat().st_mode & 0o777 == 0o600


def test_module_entry_catches_shared_error(monkeypatch, capsys):
    """覆盖 python -m 的双模块加载，避免错误类型不一致导致 traceback。"""
    import runpy
    import sys
    monkeypatch.setattr("shutil.which", lambda _: "/tools/adb")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=Mock(stdout=b"List of devices attached\ntest\tdevice\n")))
    def blocked(*args):
        raise ProbeError("VISION_CONFIG_MISSING")
    monkeypatch.setattr("experiments.marketing_call_agent.observe.observe", blocked)
    monkeypatch.setattr(sys, "argv", ["cli", "observe", "--serial", "test", "--target", "搜索", "--output", "/tmp/unused-e0", "--confirm-test-content"])
    monkeypatch.delitem(sys.modules, "experiments.marketing_call_agent.cli", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("experiments.marketing_call_agent.cli", run_name="__main__")
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "VISION_CONFIG_MISSING"


@pytest.mark.parametrize("state", ["unauthorized", "offline", "no permissions"])
def test_device_not_ready(monkeypatch,state):
    monkeypatch.setattr("shutil.which", lambda _: "/tools/adb")
    monkeypatch.setattr(subprocess,"run",Mock(return_value=Mock(stdout=f"test\t{state}\n".encode())))
    with pytest.raises(ProbeError):
        Device("adb","test").check()


@pytest.mark.parametrize("endpoint", ["https://open.bigmodel.cn:9999/api/paas/v4/chat/completions", "https://other.example/chat/completions", "http://open.bigmodel.cn/api/paas/v4/chat/completions", "https://open.bigmodel.cn/wrong"])
def test_reject_endpoint_before_capture(monkeypatch,tmp_path,endpoint):
    monkeypatch.setenv("MCA_VISION_ENDPOINT",endpoint)
    monkeypatch.setenv("MCA_VISION_API_KEY","test-secret")
    device=Mock()
    with pytest.raises(ProbeError):
        observe(device,"搜索",tmp_path/"new")
    device.screenshot.assert_not_called()


def test_http_error_does_not_expose_response(monkeypatch):
    def handler(request):
        return httpx.Response(403,text="secret-account-detail")
    original_client=httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kwargs:original_client(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(ProbeError) as exc:
        locate(Image.new("RGB",(100,200)),"搜索","frame","https://open.bigmodel.cn/api/paas/v4/chat/completions","secret")
    assert "secret" not in str(exc.value)


def test_non_integer_scaled_geometry():
    assert map_box([1,1,575,1279],(576,1280),(1080,2400))==[1,1,1079,2399]


@pytest.mark.parametrize("serial", [None, "", "-s", "two devices"])
def test_serial_rejected_before_adb(monkeypatch, serial):
    monkeypatch.setattr("shutil.which", lambda _: "/tools/adb")
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ProbeError):
        Device("adb", serial)
    run.assert_not_called()


def test_existing_output_rejected_without_capture(monkeypatch, tmp_path):
    monkeypatch.setenv("MCA_VISION_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    monkeypatch.setenv("MCA_VISION_API_KEY", "fictional")
    marker = tmp_path / "keep.txt"
    marker.write_text("保留", encoding="utf-8")
    device = Mock()
    with pytest.raises(ProbeError, match="OUTPUT_REJECTED"):
        observe(device, "搜索", tmp_path)
    assert marker.read_text(encoding="utf-8") == "保留"
    device.screenshot.assert_not_called()
