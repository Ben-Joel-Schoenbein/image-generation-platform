"""Qwen Edit 2511 and Rapid AIO routing shared by the website and Discord API."""
import copy
import math
import secrets
import json
from lora_support import apply_loras

MODEL_FILE = "Qwen-Rapid-AIO-NSFW-v19.safetensors"
RAPID_MODEL = "rapid_aio_v19"
RAPID_MODELS = {
    "rapid_aio_v19": "Qwen-Rapid-AIO-NSFW-v19.safetensors",
    "rapid_aio_v23_nsfw": "Qwen-Rapid-AIO-NSFW-v23.safetensors",
}
EDIT2511_MODELS = {
    "qwen_edit_2511_fp8": "qwen_image_edit_2511_fp8mixed.safetensors",
    "qwen_edit_2511_bf16": "qwen_image_edit_2511_bf16.safetensors",
}
EDIT2511_TEXT_ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
EDIT2511_VAE = "qwen_image_vae.safetensors"
MODEL_LIMITS = {"qwen21": 10, **{model: 4 for model in RAPID_MODELS},
                **{model: 3 for model in EDIT2511_MODELS}}


def is_rapid_model(model):
    return model in RAPID_MODELS


def is_edit2511_model(model):
    return model in EDIT2511_MODELS


def is_qwen_edit_model(model):
    return is_rapid_model(model) or is_edit2511_model(model)


def validate_generation_model(model, mode, reference_count, rapid_steps=4, edit_steps=40):
    if model not in MODEL_LIMITS:
        raise ValueError("Choose a supported Qwen Image 2.1, Edit 2511 or Rapid AIO model")
    if is_rapid_model(model):
        if isinstance(rapid_steps, bool) or not isinstance(rapid_steps, int) or rapid_steps not in {4, 6, 8}:
            raise ValueError("Rapid AIO supports 4, 6 or 8 sampling steps")
        if reference_count > 4:
            raise ValueError("Qwen Rapid AIO supports at most 4 reference images; choose Qwen Image 2.1 for up to 10")
    if is_edit2511_model(model):
        if isinstance(edit_steps, bool) or not isinstance(edit_steps, int) or edit_steps not in {20, 30, 40}:
            raise ValueError("Qwen Image Edit 2511 supports 20, 30 or 40 sampling steps")
        if reference_count > 3:
            raise ValueError("Qwen Image Edit 2511 supports at most 3 reference images; choose Qwen Image 2.1 for up to 10")
    if mode not in {"text", "edit"}:
        raise ValueError("Invalid generation mode")


def output_dimensions(quality, source_size=None):
    if quality not in {"standard", "high"}:
        raise ValueError("Choose standard (1K) or high (2K) quality")
    side = 2048 if quality == "high" else 1024
    if source_size:
        width, height = source_size
        if width <= 0 or height <= 0:
            raise ValueError("Invalid reference dimensions")
        ratio = width / height
        if not 0.25 <= ratio <= 4:
            raise ValueError("Qwen Edit reference 1 must have an aspect ratio between 1:4 and 4:1")
    else:
        ratio = 1
    return (max(64, round(side * math.sqrt(ratio) / 64) * 64),
            max(64, round(side / math.sqrt(ratio) / 64) * 64))


def build_rapid_workflow(mode, uploads, prompt, quality="standard", seed=None,
                         rapid_steps=4, source_size=None, enhancer_template=None, model=RAPID_MODEL, loras=None):
    validate_generation_model(model, mode, len(uploads), rapid_steps)
    if not is_rapid_model(model):
        raise ValueError("Choose a Rapid AIO checkpoint for this workflow")
    if mode == "edit" and not uploads:
        raise ValueError("Upload at least one reference image for edit mode")
    if mode == "text" and uploads:
        raise ValueError("Choose edit mode to use reference images")
    if seed is None:
        seed = secrets.randbelow(2**32)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("Seed must be between 0 and 4294967295")
    width, height = output_dimensions(quality, source_size)
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": RAPID_MODELS[model]}},
        "2": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "3": {"class_type": "StudioRapidAIOTextEncode", "inputs": {"clip": ["1", 1], "vae": ["1", 2],
                  "latent": ["2", 0], "prompt": prompt}},
        "4": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["1", 1], "vae": ["1", 2], "prompt": ""}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                  "latent_image": ["2", 0], "seed": seed, "steps": rapid_steps, "cfg": 1.0,
                  "sampler_name": "euler_ancestral", "scheduler": "beta", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "studio-" + model.replace("_", "-")}},
    }
    for index, upload in enumerate(uploads, 1):
        name = upload.get("name", "")
        if not name:
            raise ValueError("ComfyUI returned an empty reference filename")
        subfolder = upload.get("subfolder", "")
        image = f"{subfolder}/{name}" if subfolder else name
        node_id = str(100 + index)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": image}}
        graph["3"]["inputs"][f"image{index}"] = [node_id, 0]
    if enhancer_template is not None:
        # Reuse the installed Qwen 2.1 PE model and its actual system prompt.
        # The image generator/encoder/VAE remain those of the AIO checkpoint.
        template = copy.deepcopy(enhancer_template)
        for node_id in ("9", "10", "12"):
            if node_id not in template:
                raise ValueError("The installed workflow lacks Qwen prompt expansion nodes; disable Expand description")
            graph[node_id] = template[node_id]
        if graph["10"].get("class_type") != "TextGenerate":
            raise ValueError("Unsupported prompt expansion workflow; disable Expand description")
        graph["10"]["inputs"]["prompt"] = prompt
        graph["10"]["inputs"]["sampling_mode.seed"] = seed
        if mode == "edit":
            if template.get("11", {}).get("class_type") != "BatchImagesNode":
                raise ValueError("Unsupported reference prompt expansion workflow")
            graph["11"] = {"class_type": "BatchImagesNode", "inputs": {
                f"images.image{i}": [str(101 + i), 0] for i in range(len(uploads))}}
            graph["10"]["inputs"]["image"] = ["11", 0]
        else:
            graph["10"]["inputs"].pop("image", None)
        graph["3"]["inputs"]["prompt"] = ["12", 0]
        # Refuse unexpected dependency IDs instead of silently producing a broken graph.
        for node in graph.values():
            for value in node.get("inputs", {}).values():
                if isinstance(value, list) and len(value) == 2 and value[0] not in graph:
                    raise ValueError("Unexpected prompt expansion dependency; disable Expand description")
    return apply_loras(graph, loras)


def build_edit2511_workflow(mode, uploads, prompt, quality="standard", seed=None,
                            edit_steps=40, source_size=None, enhancer_template=None,
                            model="qwen_edit_2511_fp8", loras=None):
    """Use the official full-step 2511 model path with separate encoder and VAE.

    Reference preparation and optional PE nodes are shared with the AIO route.
    Every checkpoint-dependent input and all sampling settings are replaced.
    """
    validate_generation_model(model, mode, len(uploads), edit_steps=edit_steps)
    if not is_edit2511_model(model):
        raise ValueError("Choose a Qwen Image Edit 2511 FP8 or BF16 model")
    graph = build_rapid_workflow(mode, uploads, prompt, quality, seed, 4,
                                 source_size, enhancer_template, loras=None)
    graph["1"] = {"class_type": "UNETLoader", "inputs": {
        "unet_name": EDIT2511_MODELS[model], "weight_dtype": "default"}}
    graph["20"] = {"class_type": "CLIPLoader", "inputs": {
        "clip_name": EDIT2511_TEXT_ENCODER, "type": "qwen_image", "device": "default"}}
    graph["21"] = {"class_type": "VAELoader", "inputs": {"vae_name": EDIT2511_VAE}}
    graph["22"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {
        "model": ["1", 0], "shift": 3.1}}
    graph["23"] = {"class_type": "CFGNorm", "inputs": {
        "model": ["22", 0], "strength": 1.0, "pre_cfg": False}}
    graph["3"]["inputs"].update(clip=["20", 0], vae=["21", 0])
    # Both branches carry identical references, as in the official Edit template.
    graph["4"] = {"class_type": "StudioRapidAIOTextEncode", "inputs": {
        **copy.deepcopy(graph["3"]["inputs"]), "prompt": ""}}
    graph["5"]["inputs"].update(model=["23", 0], steps=edit_steps, cfg=4.0,
                                 sampler_name="euler", scheduler="simple")
    graph["6"]["inputs"]["vae"] = ["21", 0]
    graph["7"]["inputs"]["filename_prefix"] = "studio-" + model.replace("_", "-")
    return apply_loras(graph, loras)


def rapid_controls():
    return '''<label>Model<select name="model" id="generation-model">
<option value="qwen21">Qwen Image 2.1</option>
<option value="rapid_aio_v19">Qwen Rapid AIO v19 — NSFW</option>
<option value="rapid_aio_v23_nsfw">Qwen Rapid AIO v23 — NSFW</option>
<option value="qwen_edit_2511_fp8">Qwen Image Edit 2511 — FP8 Mixed</option>
<option value="qwen_edit_2511_bf16">Qwen Image Edit 2511 — BF16</option></select></label>
<label id="rapid-steps-label" hidden>Sampling steps<select name="rapid_steps" id="rapid-steps" disabled>
<option value="4">4 — fast</option><option value="6">6</option><option value="8">8 — more detail</option></select></label>
<label id="edit-steps-label" hidden>Edit 2511 sampling steps<select name="edit_steps" id="edit-steps" disabled>
<option value="20">20 — fast</option><option value="30">30</option><option value="40" selected>40 — recommended</option></select></label>
<p id="rapid-model-help" class="muted" hidden>Rapid AIO accepts up to 4 reference images. Output follows Image 1's aspect ratio. Expand description is optional and can take several minutes.</p>
<p id="edit-model-help" class="muted" hidden>Edit 2511 accepts up to 3 reference images. These are base models. FP8 Mixed uses less memory; BF16 may require CPU offloading. Standard and Heretic prompt expansion are optional.</p>
<p id="rapid-lora-help" class="muted" hidden>Detected LoRAs are applied automatically using the shared admin settings.</p>
<script>
document.addEventListener('DOMContentLoaded', () => {
  const rapidModels = __RAPID_MODELS__;
  const editModels = __EDIT_MODELS__;
  const form = document.getElementById('generation-form');
  if (!form) return;
  const model = form.elements.model, picker = form.elements.references;
  const enhancer = form.elements.prompt_expansion || form.elements.enhance_prompt;
  const readEnhancer = () => enhancer.tagName === 'SELECT' ? enhancer.value : enhancer.checked;
  const writeEnhancer = value => { if (enhancer.tagName === 'SELECT') enhancer.value = value; else enhancer.checked = value; };
  const steps = form.elements.rapid_steps;
  const stepLabel = document.getElementById('rapid-steps-label');
  const help = document.getElementById('rapid-model-help');
  const loraHelp = document.getElementById('rapid-lora-help');
  const editSteps = form.elements.edit_steps;
  const editStepLabel = document.getElementById('edit-steps-label');
  const editHelp = document.getElementById('edit-model-help');
  let previous = model.value;
  const preferences = {qwen21: readEnhancer(), rapid_aio_v19: enhancer.tagName === 'SELECT' ? 'off' : false};
  const check = () => {
    const limit = editModels.includes(model.value) ? 3 : rapidModels.includes(model.value) ? 4 : 10;
    picker.setCustomValidity(picker.files.length > limit ? `Choose at most ${limit} reference images for this model` : '');
    const label = picker.closest('label');
    if (label && label.firstChild.nodeType === 3) label.firstChild.textContent = `Reference images (1–${limit} for edit mode)`;
  };
  const update = () => {
    const rapid = rapidModels.includes(model.value);
    const edit = editModels.includes(model.value);
    steps.disabled = !rapid; stepLabel.hidden = !rapid; help.hidden = !rapid;
    if (editSteps) editSteps.disabled = !edit;
    if (editStepLabel) editStepLabel.hidden = !edit;
    if (editHelp) editHelp.hidden = !edit;
    if (loraHelp) loraHelp.hidden = false;
    check();
  };
  model.addEventListener('change', () => {
    preferences[previous] = readEnhancer();
    writeEnhancer(preferences[model.value] ?? (enhancer.tagName === 'SELECT' ? 'off' : false));
    previous = model.value; update();
  });
  picker.addEventListener('change', check);
  update();
});
</script>'''.replace('__RAPID_MODELS__', json.dumps(list(RAPID_MODELS))).replace('__EDIT_MODELS__', json.dumps(list(EDIT2511_MODELS)))
