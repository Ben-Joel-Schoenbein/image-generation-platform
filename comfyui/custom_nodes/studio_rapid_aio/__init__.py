"""Four independent Qwen Edit reference inputs, scaled using the output latent.

Based on Comfy-Org/ComfyUI comfy_extras/nodes_qwen.py (GPL-3.0).
This file is distributed under GPL-3.0; see NOTICE.md.
"""
import math

import comfy.utils
import node_helpers


class StudioRapidAIOTextEncode:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"clip": ("CLIP",), "vae": ("VAE",), "latent": ("LATENT",),
                             "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True})},
                "optional": {f"image{i}": ("IMAGE",) for i in range(1, 5)}}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "encode"
    CATEGORY = "conditioning/qwen"

    def encode(self, clip, vae, latent, prompt, image1=None, image2=None, image3=None, image4=None):
        ref_latents, vision_images = [], []
        out_height = latent["samples"].shape[-2] * 8
        out_width = latent["samples"].shape[-1] * 8
        area = out_width * out_height
        image_prompt = ""
        image_index = 0
        for image in (image1, image2, image3, image4):
            if image is None:
                continue
            image_index += 1
            samples = image.movedim(-1, 1)
            height, width = samples.shape[2:]
            scale = math.sqrt(384 * 384 / (width * height))
            vision = comfy.utils.common_upscale(samples, max(1, round(width * scale)),
                                                max(1, round(height * scale)), "area", "disabled")
            vision_images.append(vision.movedim(1, -1)[:, :, :, :3])
            if image_index == 1:
                # Backend chooses the first reference's aspect ratio. Align its
                # appearance latent with the actual output dimensions.
                ref_width, ref_height = out_width, out_height
            else:
                scale = math.sqrt(area / (width * height))
                ref_width = max(8, round(width * scale / 8) * 8)
                ref_height = max(8, round(height * scale / 8) * 8)
            appearance = comfy.utils.common_upscale(samples, ref_width, ref_height, "area", "disabled")
            ref_latents.append(vae.encode(appearance.movedim(1, -1)[:, :, :, :3]))
            image_prompt += f"Picture {image_index}: <|vision_start|><|image_pad|><|vision_end|>"
        template = (
            "<|im_start|>system\nDescribe the key features of the input image (color, shape, size, texture, "
            "objects, background), then explain how the user's text instruction should alter or modify "
            "the image. Generate a new image that meets the user's requirements while maintaining "
            "consistency with the original input where appropriate.<|im_end|>\n"
            "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n"
        )
        tokens = clip.tokenize(image_prompt + prompt, images=vision_images, llama_template=template)
        conditioning = clip.encode_from_tokens_scheduled(tokens)
        if ref_latents:
            conditioning = node_helpers.conditioning_set_values(conditioning, {"reference_latents": ref_latents}, append=True)
        return (conditioning,)


NODE_CLASS_MAPPINGS = {"StudioRapidAIOTextEncode": StudioRapidAIOTextEncode}
NODE_DISPLAY_NAME_MAPPINGS = {"StudioRapidAIOTextEncode": "Qwen Rapid AIO — up to 4 references"}


class StudioCapturePrompt:
    """Publish the final prompt through history and executed websocket events."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"text": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING",)
    FUNCTION = "capture"
    CATEGORY = "text/studio"
    OUTPUT_NODE = True

    def capture(self, text):
        return {"ui": {"text": [text]}, "result": (text,)}


NODE_CLASS_MAPPINGS["StudioCapturePrompt"] = StudioCapturePrompt
NODE_DISPLAY_NAME_MAPPINGS["StudioCapturePrompt"] = "Image Studio — expanded description"
