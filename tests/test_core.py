"""纯函数层的单元测试：不碰网络、不碰磁盘（除临时目录）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from seed_image.comfy.waiting import wait_for_jobs
from seed_image.core import naming
from seed_image.core.imageparams import build_request
from seed_image.core.values import (
    UploadRequest,
    apply_assignments,
    parse_assignment,
    resolve_uploads,
    uploads_in,
)
from seed_image.core.workflow import inspect_inputs, validate_workflow
from seed_image.errors import AppError

WORKFLOW = {
    "7": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "model": ["4", 0]}},
    "75:74": {"class_type": "CLIPTextEncode", "inputs": {"text": "old", "clip": ["4", 1]}},
    "10": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
}


def test_value_types_are_inferred():
    assert parse_assignment("7.inputs.steps=30").value == 30
    assert parse_assignment("7.inputs.seed=42").value == 42
    assert parse_assignment("75:74.inputs.text=一只柯基").value == "一只柯基"
    assert parse_assignment("7.inputs.flag=true").value is True


def test_node_id_colon_is_not_a_separator():
    assert parse_assignment("75:74.inputs.text=x").path == ("75:74", "inputs", "text")
    assert parse_assignment("7.inputs.a[2]=1").path == ("7", "inputs", "a", "2")


def test_only_first_equals_splits():
    item = parse_assignment("75:74.inputs.text=a=b")
    assert item.value == "a=b"


def test_upload_marker_is_recognised_anywhere():
    single = parse_assignment("10.inputs.image=@upload{./cat.png}")
    nested = parse_assignment('10.inputs.images=["@upload{a.png}", "b.png"]')
    assert single.value == UploadRequest(Path("./cat.png"))
    assert [u.local_path for u in uploads_in([single, nested])] == [Path("cat.png"), Path("a.png")]
    resolved = resolve_uploads(nested, {Path("a.png"): "a (1).png"})
    assert resolved.value == ["a (1).png", "b.png"]


def test_apply_does_not_mutate_input():
    patched = apply_assignments(WORKFLOW, [parse_assignment("7.inputs.steps=30")])
    assert patched["7"]["inputs"]["steps"] == 30
    assert WORKFLOW["7"]["inputs"]["steps"] == 20


def test_unknown_path_is_rejected_with_context():
    with pytest.raises(AppError) as caught:
        apply_assignments(WORKFLOW, [parse_assignment("7.inputs.stepz=30")])
    assert caught.value.code == "path_not_found"
    assert "steps" in caught.value.details["available"]


def test_missing_equals_is_rejected():
    with pytest.raises(AppError) as caught:
        parse_assignment("7.inputs.steps")
    assert caught.value.code == "invalid_values"


def test_ui_format_workflow_is_rejected():
    with pytest.raises(AppError) as caught:
        validate_workflow({"nodes": [], "links": []}, Path("wf.json"))
    assert caught.value.code == "workflow_format_ui"


def test_inspect_skips_links():
    paths = [slot.path for slot in inspect_inputs(WORKFLOW)]
    assert paths == ["7.inputs.seed", "7.inputs.steps", "75:74.inputs.text", "10.inputs.image"]


def test_background_is_passed_through():
    request = build_request(
        prompt="p", model="gpt-image-2", size="auto", quality="auto",
        background="transparent", number="1",
    )
    assert request.body["background"] == "transparent"


def test_other_models_keep_background():
    request = build_request(
        prompt="p", model="gpt-image-1", size="auto", quality="auto",
        background="transparent", number="2",
    )
    assert request.body["background"] == "transparent"
    assert request.body["n"] == 2


def test_slug_strips_illegal_characters_and_truncates():
    assert naming.slug('夜景:很美/宽阔?啊') == "夜景很美宽阔啊"
    assert naming.slug("一" * 30) == "一" * naming.SLUG_LIMIT
    assert naming.slug("  ...  ") == "untitled"
    assert naming.slug("con") == "_con"


def test_image_name_shape():
    assert naming.image_name("夜景", 3, "abcd1234", "png") == "夜景-03-abcd1234.png"


def test_prompt_slug_turns_punctuation_into_underscore():
    assert naming.prompt_slug("夜景：很美，宽阔！啊") == "夜景_很美_宽阔_啊"
    assert naming.prompt_slug("睡前故事·月亮（第二版）") == "睡前故事_月亮_第二版"
    # 连续的标点只算一个分隔符，首尾的不留
    assert naming.prompt_slug("  <<夜景>>...  ") == "夜景"
    assert naming.prompt_slug("夜景😀很美") == "夜景_很美"
    assert naming.prompt_slug("一" * 30) == "一" * naming.SLUG_LIMIT
    # 截断刚好落在分隔符上时，不留尾巴
    assert naming.prompt_slug("一" * 19 + "-二三") == "一" * 19
    assert naming.prompt_slug("  ...  ") == "untitled"
    assert naming.prompt_slug("a cat, neon night!") == "a_cat_neon_night"
    assert naming.prompt_slug("con") == "_con"
    assert naming.image_name("夜景,很美", 1, "abcd1234", "png") == "夜景_很美-01-abcd1234.png"
    assert naming.sidecar_name("夜景,很美", "abcd1234") == "夜景_很美-abcd1234.json"


def test_wait_reports_unfinished_on_timeout():
    outcome = wait_for_jobs(lambda job_id: {"status": "in_progress"}, ["a"], timeout=0.0, poll_interval=0.01)
    assert outcome.timed_out
    assert list(outcome.unfinished) == ["a"]


def test_wait_returns_terminal_snapshot():
    outcome = wait_for_jobs(lambda job_id: {"status": "completed"}, ["a", "b"], timeout=1.0)
    assert not outcome.timed_out
    assert set(outcome.finished) == {"a", "b"}
