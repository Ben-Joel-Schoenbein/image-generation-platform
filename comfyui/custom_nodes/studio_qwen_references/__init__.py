# SPDX-License-Identifier: GPL-3.0-or-later
# Based on ComfyUI's TextEncodeQwenImageEditPlus, GPL-3.0.
# Upstream: Comfy-Org/ComfyUI, commit f1072eb0350638a3390ddb6afbcaa8c6b237c6fd,
# comfy_extras/nodes_qwen.py. See LICENSE in this directory.
"""Qwen Edit conditioning with up to ten separate reference images.

The model card recommends 1–3 images. More images are supported by this
extension but result quality and GPU memory use need testing on the host.
"""
import math

import comfy.utils
import node_helpers


LLAMA_TEMPLATE = (
    "<|im_start|>system\nDescribe the key features of the input image (color, shape, size, texture, objects, background), "
    "then explain how the user's text instruction should alter or modify the image. Generate a new image that meets "
    "the user's requirements while maintaining consistency with the original input where appropriate.<|im_end|>\n"
    "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n"
)


def scaled_image(image, target_pixels, multiple=1):
    samples = image[:1, :, :, :3].movedim(-1, 1)
    factor = math.sqrt(target_pixels / (samples.shape[2] * samples.shape[3]))
    width = max(multiple, round(samples.shape[3] * factor / multiple) * multiple)
    height = max(multiple, round(samples.shape[2] * factor / multiple) * multiple)
    return comfy.utils.common_upscale(samples, width, height, "area", "disabled").movedim(1, -1)


class StudioQwenImageEditReferences:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip": ("CLIP",), "vae": ("VAE",),
                "prompt": ("STRING", {"multiline": True}),
                "image1": ("IMAGE",),
                "reference_megapixels": ("FLOAT", {"default": 3.0, "min": 0.5, "max": 10.0, "step": 0.5}),
            },
            "optional": {f"image{i}": ("IMAGE",) for i in range(2, 11)},
        }

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING")
    RETURN_NAMES = ("positive", "negative")
    FUNCTION = "encode"
    CATEGORY = "Image Studio/Qwen"

    def encode(self, clip, vae, prompt, image1, reference_megapixels=3.0, **kwargs):
        images = [image1] + [kwargs.get(f"image{i}") for i in range(2, 11)]
        images = [image for image in images if image is not None]
        # Keep the total reference token budget bounded as image count grows.
        pixels_per_reference = min(1024 * 1024, reference_megapixels * 1024 * 1024 / len(images))
        vision_images = [scaled_image(image, 384 * 384) for image in images]
        latents = [vae.encode(scaled_image(image, pixels_per_reference, multiple=8)) for image in images]
        prefix = "".join(f"Picture {i}: <|vision_start|><|image_pad|><|vision_end|>" for i in range(1, len(images) + 1))

        def condition(text):
            tokens = clip.tokenize(prefix + text, images=vision_images, llama_template=LLAMA_TEMPLATE)
            encoded = clip.encode_from_tokens_scheduled(tokens)
            return node_helpers.conditioning_set_values(encoded, {"reference_latents": latents}, append=True)

        return condition(prompt), condition("")


NODE_CLASS_MAPPINGS = {"StudioQwenImageEditReferences": StudioQwenImageEditReferences}
NODE_DISPLAY_NAME_MAPPINGS = {"StudioQwenImageEditReferences": "Image Studio · Qwen Edit (1–10 references)"}
