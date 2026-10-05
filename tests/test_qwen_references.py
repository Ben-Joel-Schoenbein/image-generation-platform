import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


class Tensor:
    def __init__(self, shape, marker):
        self.shape = shape
        self.marker = marker

    def __getitem__(self, index):
        # The node deliberately uses only the first frame and RGB channels.
        return Tensor((1, self.shape[1], self.shape[2], 3), self.marker)

    def movedim(self, source, target):
        axes = list(self.shape)
        axis = axes.pop(source)
        axes.insert(target if target >= 0 else len(axes) + 1 + target, axis)
        return Tensor(tuple(axes), self.marker)


@pytest.mark.parametrize("count", [1, 3, 10])
def test_qwen_node_conditions_on_each_separate_image_with_bounded_pixel_budget(monkeypatch, count):
    comfy = types.ModuleType("comfy")
    utils = types.ModuleType("comfy.utils")

    def upscale(samples, width, height, method, crop):
        return Tensor((1, 3, height, width), samples.marker)

    utils.common_upscale = upscale
    comfy.utils = utils
    helpers = types.ModuleType("node_helpers")
    helpers.conditioning_set_values = lambda encoded, values, append: (encoded, values)
    monkeypatch.setitem(sys.modules, "comfy", comfy)
    monkeypatch.setitem(sys.modules, "comfy.utils", utils)
    monkeypatch.setitem(sys.modules, "node_helpers", helpers)
    spec = importlib.util.spec_from_file_location("qwen_node_under_test", ROOT / "comfyui/custom_nodes/studio_qwen_references/__init__.py")
    node = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(node)
    clip_calls = []
    vae_calls = []

    class Clip:
        def tokenize(self, prompt, images, llama_template):
            clip_calls.append((prompt, images))
            return prompt

        def encode_from_tokens_scheduled(self, tokens):
            return tokens

    class Vae:
        def encode(self, image):
            vae_calls.append(image)
            return image.marker

    images = [Tensor((2, 480, 640, 4), marker=i) for i in range(count)]
    positive, negative = node.StudioQwenImageEditReferences().encode(Clip(), Vae(), "combine all images", images[0],
        **{f"image{i + 1}": image for i, image in enumerate(images[1:], start=1)})
    assert positive[1]["reference_latents"] == list(range(count))
    assert negative[1]["reference_latents"] == list(range(count))
    assert len(vae_calls) == count  # Do not encode the same references again for negative conditioning.
    assert sum(image.shape[1] * image.shape[2] for image in vae_calls) <= 3 * 1024 * 1024 * 1.02
    for prompt, vision in clip_calls:
        assert len(vision) == count
        assert [image.marker for image in vision] == list(range(count))
        assert f"Picture {count}:" in prompt
    assert all(image.shape[0] == 1 and image.shape[-1] == 3 for image in vae_calls)
    schema = node.StudioQwenImageEditReferences.INPUT_TYPES()
    assert len(schema["optional"]) == 9
    assert "image10" in schema["optional"]
