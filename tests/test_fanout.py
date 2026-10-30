"""--multi 节点复制的单元测试。schema 片段照抄自真实 ComfyUI 的 /object_info。"""

from __future__ import annotations

import pytest

from seed_image.core.fanout import expand, plan_multi, slot_family
from seed_image.core.values import parse_assignment
from seed_image.errors import AppError

IMG = ["IMAGE", {}]
SCHEMAS = {
    "TextEncodeQwenImage21": {"input": {
        "required": {
            "prompt": ["STRING", {}],
            "images": ["COMFY_AUTOGROW_V3", {"template": {
                "input": {"required": {"image": IMG}},
                "names": [f"image_{i}" for i in range(1, 17)], "min": 0,
            }}],
        },
        "optional": {"vae": ["VAE", {}]},
    }},
    "BatchImagesNode": {"input": {"required": {
        "images": ["COMFY_AUTOGROW_V3", {"template": {
            "input": {"required": {"image": IMG}}, "prefix": "image", "min": 1, "max": 50,
        }}],
    }}},
    "TextEncodeQwenImageEditPlus": {"input": {
        "required": {"prompt": ["STRING", {}]},
        "optional": {"vae": ["VAE", {}], "image1": IMG, "image2": IMG, "image3": IMG},
    }},
    "SeedLLM": {"input": {"required": {
        "model": ["COMFY_DYNAMICCOMBO_V3", {"options": [
            {"key": "pro", "inputs": {"required": {
                "images": ["COMFY_AUTOGROW_V3", {"template": {
                    "input": {"required": {"image": IMG}}, "names": ["image_1", "image_2"], "min": 0,
                }}],
            }}},
            {"key": "lite", "inputs": {"required": {}}},
        ]}],
    }}},
    "ImageScale": {"input": {"required": {"image": IMG, "width": ["INT", {}]}}},
    "GetImageSize": {"input": {"required": {"image": IMG}}},
    "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}]}}},
    "VAEEncode": {"input": {"required": {"pixels": IMG, "vae": ["VAE", {}]}}},
    "ReferenceLatent": {"input": {"required": {"conditioning": ["CONDITIONING", {}]},
                                  "optional": {"latent": ["LATENT", {}]}}},
    "LoadImage": {"input": {"required": {"image": [["a.png"], {}]}}},
}
schema = SCHEMAS.get


def load(node_id="470"):
    return {node_id: {"class_type": "LoadImage", "inputs": {"image": ""}}}


def turbo():
    """qwen-edit-turbo 的骨架：一张图同时喂给编码器和拼批节点。"""
    return load() | {
        "459:454": {"class_type": "VAELoader", "inputs": {}},
        "459:474": {"class_type": "TextEncodeQwenImage21",
                    "inputs": {"prompt": "p", "images.image_1": ["470", 0], "vae": ["459:454", 0]}},
        "459:485": {"class_type": "BatchImagesNode", "inputs": {"images.image0": ["470", 0]}},
    }


def test_autogrow_names_and_prefix_both_expand():
    wf, exp = expand(turbo(), "470", 2, schema)
    assert exp.copies == ("470", "470_2", "470_3")
    enc = wf["459:474"]["inputs"]
    assert enc["images.image_2"] == ["470_2", 0] and enc["images.image_3"] == ["470_3", 0]
    batch = wf["459:485"]["inputs"]
    assert batch["images.image1"] == ["470_2", 0] and batch["images.image2"] == ["470_3", 0]
    assert enc["vae"] == ["459:454", 0]  # 共享节点不被复制
    assert "459:454_2" not in wf


def test_input_is_not_mutated():
    original = turbo()
    expand(original, "470", 1, schema)
    assert original == turbo()


def test_plan_multi_rewrites_values_onto_copies():
    items = [parse_assignment(f"470.inputs.image={name}") for name in ("a.png", "b.png", "c.png")]
    wf, rewritten, expansions = plan_multi(turbo(), items, schema)
    assert [(a.path, a.value) for a in rewritten] == [
        (("470", "inputs", "image"), "a.png"),
        (("470_2", "inputs", "image"), "b.png"),
        (("470_3", "inputs", "image"), "c.png"),
    ]
    assert expansions[0].copies == ("470", "470_2", "470_3")


def test_single_multi_is_a_plain_assignment():
    wf, rewritten, _ = plan_multi(turbo(), [parse_assignment("470.inputs.image=a.png")], schema)
    assert wf == turbo()
    assert rewritten[0].path == ("470", "inputs", "image")


def test_numbered_legacy_inputs_expand_up_to_capacity():
    wf = load() | {"9": {"class_type": "TextEncodeQwenImageEditPlus",
                         "inputs": {"prompt": "p", "image1": ["470", 0]}}}
    out, _ = expand(wf, "470", 2, schema)
    assert out["9"]["inputs"]["image2"] == ["470_2", 0]
    assert out["9"]["inputs"]["image3"] == ["470_3", 0]
    with pytest.raises(AppError) as caught:
        expand(wf, "470", 3, schema)
    assert caught.value.code == "multi_slot_full"
    assert caught.value.details["capacity"] == 3


def test_autogrow_nested_in_dynamic_combo():
    node = {"class_type": "SeedLLM", "inputs": {"model": "pro", "model.images.image_1": ["470", 0]}}
    assert slot_family(node, "model.images.image_1", schema) == ["model.images.image_1", "model.images.image_2"]
    lite = {"class_type": "SeedLLM", "inputs": {"model": "lite"}}
    assert slot_family(lite, "model.images.image_1", schema) is None


def test_private_upstream_chain_is_cloned():
    wf = load() | {
        "s": {"class_type": "ImageScale", "inputs": {"image": ["470", 0], "width": 512}},
        "9": {"class_type": "TextEncodeQwenImage21", "inputs": {"images.image_1": ["s", 0]}},
    }
    out, _ = expand(wf, "470", 1, schema)
    assert out["s_2"]["inputs"]["image"] == ["470_2", 0]
    assert out["9"]["inputs"]["images.image_2"] == ["s_2", 0]


def test_side_branch_not_leading_to_slot_stays_with_original():
    """拿第一张图的尺寸去建 latent：这条支路不复制。"""
    wf = turbo() | {
        "sz": {"class_type": "GetImageSize", "inputs": {"image": ["470", 0]}},
        "lat": {"class_type": "EmptyLatentImage", "inputs": {"width": ["sz", 0]}},
    }
    out, _ = expand(wf, "470", 1, schema)
    assert "sz_2" not in out and "lat_2" not in out
    assert out["459:474"]["inputs"]["images.image_2"] == ["470_2", 0]


def test_new_slots_go_after_the_original_one():
    wf = turbo()
    wf["459:474"]["inputs"]["images.image_2"] = ["999", 0]  # 已被别的图占了
    wf["999"] = {"class_type": "LoadImage", "inputs": {"image": "x.png"}}
    out, _ = expand(wf, "470", 1, schema)
    assert out["459:474"]["inputs"]["images.image_3"] == ["470_2", 0]


def test_chained_reference_latent_is_rejected():
    wf = load() | {
        "e": {"class_type": "VAEEncode", "inputs": {"pixels": ["470", 0], "vae": ["v", 0]}},
        "r": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["c", 0], "latent": ["e", 0]}},
    }
    with pytest.raises(AppError) as caught:
        expand(wf, "470", 1, schema)
    assert caught.value.code == "multi_no_slot"


def test_offline_guess_only_trusts_dotted_numbered_keys():
    wf, _ = expand(turbo(), "470", 1, None)
    assert wf["459:474"]["inputs"]["images.image_2"] == ["470_2", 0]
    assert wf["459:485"]["inputs"]["images.image1"] == ["470_2", 0]
    plain = load() | {"9": {"class_type": "X", "inputs": {"image1": ["470", 0]}}}
    with pytest.raises(AppError) as caught:
        expand(plain, "470", 1, None)
    assert caught.value.code == "multi_no_slot"


def test_unknown_node_and_count_mismatch():
    with pytest.raises(AppError) as caught:
        expand(turbo(), "nope", 1, schema)
    assert caught.value.code == "multi_node_not_found"
    items = [parse_assignment("470.inputs.image=a"), parse_assignment("470.inputs.image=b"),
             parse_assignment("470.inputs.upload=x")]
    with pytest.raises(AppError) as caught:
        plan_multi(turbo(), items, schema)
    assert caught.value.code == "multi_count_mismatch"


def test_copy_ids_never_collide():
    wf = turbo() | {"470_2": {"class_type": "LoadImage", "inputs": {"image": "x"}}}
    out, exp = expand(wf, "470", 1, schema)
    assert exp.copies == ("470", "470_2_2")
    assert out["470_2"] == {"class_type": "LoadImage", "inputs": {"image": "x"}}
