"""Qwen Rapid AIO v19 routing shared by the website and Discord API."""
import copy
import math
import secrets

MODEL_FILE = "Qwen-Rapid-AIO-NSFW-v19.safetensors"
RAPID_MODEL = "rapid_aio_v19"
MODEL_LIMITS = {"qwen21": 10, RAPID_MODEL: 4}


def validate_generation_model(model, mode, reference_count, rapid_steps=4):
    if model not in MODEL_LIMITS:
        raise ValueError("Choose Qwen Image 2.1 or Qwen Rapid AIO v19")
    if model == RAPID_MODEL:
        if isinstance(rapid_steps, bool) or not isinstance(rapid_steps, int) or rapid_steps not in {4, 6, 8}:
            raise ValueError("Rapid AIO supports 4, 6 or 8 sampling steps")
        if reference_count > 4:
            raise ValueError("Qwen Rapid AIO v19 supports at most 4 reference images; choose Qwen Image 2.1 for up to 10")
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
            raise ValueError("Rapid AIO reference 1 must have an aspect ratio between 1:4 and 4:1")
    else:
        ratio = 1
    return (max(64, round(side * math.sqrt(ratio) / 64) * 64),
            max(64, round(side / math.sqrt(ratio) / 64) * 64))


def build_rapid_workflow(mode, uploads, prompt, quality="standard", seed=None,
                         rapid_steps=4, source_size=None, enhancer_template=None):
    validate_generation_model(RAPID_MODEL, mode, len(uploads), rapid_steps)
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
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": MODEL_FILE}},
        "2": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "3": {"class_type": "StudioRapidAIOTextEncode", "inputs": {"clip": ["1", 1], "vae": ["1", 2],
                  "latent": ["2", 0], "prompt": prompt}},
        "4": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["1", 1], "vae": ["1", 2], "prompt": ""}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0],
                  "latent_image": ["2", 0], "seed": seed, "steps": rapid_steps, "cfg": 1.0,
                  "sampler_name": "euler_ancestral", "scheduler": "beta", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "studio-rapid-aio-v19"}},
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
    return graph


def rapid_controls():
    return '''<label>Model<select name="model" id="generation-model">
<option value="qwen21">Qwen Image 2.1</option>
<option value="rapid_aio_v19">Qwen Rapid AIO v19 — NSFW</option></select></label>
<label id="rapid-steps-label" hidden>Sampling steps<select name="rapid_steps" id="rapid-steps" disabled>
<option value="4">4 — fast</option><option value="6">6</option><option value="8">8 — more detail</option></select></label>
<p id="rapid-model-help" class="muted" hidden>Rapid AIO accepts up to 4 reference images. Output follows Image 1's aspect ratio. Expand description is optional and can take several minutes.</p>
<script>
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('generation-form');
  if (!form) return;
  const model = form.elements.model, picker = form.elements.references;
  const enhancer = form.elements.enhance_prompt;
  const steps = form.elements.rapid_steps;
  const stepLabel = document.getElementById('rapid-steps-label');
  const help = document.getElementById('rapid-model-help');
  let previous = model.value;
  const preferences = {qwen21: enhancer.checked, rapid_aio_v19: false};
  const check = () => {
    const limit = model.value === 'rapid_aio_v19' ? 4 : 10;
    picker.setCustomValidity(picker.files.length > limit ? `Choose at most ${limit} reference images for this model` : '');
    const label = picker.closest('label');
    if (label && label.firstChild.nodeType === 3) label.firstChild.textContent = `Reference images (1–${limit} for edit mode)`;
  };
  const update = () => {
    const rapid = model.value === 'rapid_aio_v19';
    steps.disabled = !rapid; stepLabel.hidden = !rapid; help.hidden = !rapid;
    check();
  };
  model.addEventListener('change', () => {
    preferences[previous] = enhancer.checked;
    enhancer.checked = preferences[model.value] ?? false;
    previous = model.value; update();
  });
  picker.addEventListener('change', check);
  update();
});
</script>'''
