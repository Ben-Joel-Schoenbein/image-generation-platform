"""Request options for the Qwen Image 2.1 API workflows."""
import copy

QWEN21_TEXT_ENCODERS = {
    "bf16": "qwen3vl_8b_bf16.safetensors",
    "int8_convrot": "qwen3vl_8b_int8_convrot.safetensors",
}
QWEN21_SAMPLERS = {"default", "euler", "er_sde"}
QWEN21_SCHEDULERS = {"default", "simple", "beta"}


def validate_qwen21_selection(model="qwen21", qwen_sampler="default",
                              qwen_scheduler="default", qwen_text_encoder="bf16"):
    if not isinstance(qwen_sampler, str) or qwen_sampler not in QWEN21_SAMPLERS:
        raise ValueError("Choose Default, Euler or ER-SDE for the Qwen 2.1 sampler")
    if not isinstance(qwen_scheduler, str) or qwen_scheduler not in QWEN21_SCHEDULERS:
        raise ValueError("Choose Default, Simple or Beta for the Qwen 2.1 scheduler")
    if not isinstance(qwen_text_encoder, str) or qwen_text_encoder not in QWEN21_TEXT_ENCODERS:
        raise ValueError("Choose BF16 or INT8 ConvRot for the Qwen 2.1 text encoder")
    if model != "qwen21" and (qwen_sampler != "default" or qwen_scheduler != "default"
                              or qwen_text_encoder != "bf16"):
        raise ValueError("Sampler, scheduler and text encoder options apply only to Qwen Image 2.1")


def validate_options(quality="standard", edit_intent="edit", seed=None):
    if quality not in {"standard", "high"}:
        raise ValueError("Choose standard (1K) or high (2K) quality")
    if edit_intent not in {"edit", "recreate"}:
        raise ValueError("Choose edit or recreate for reference images")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32):
        raise ValueError("Seed must be an integer between 0 and 4294967295")


def prepare_prompt(mode, prompt, edit_intent):
    if mode != "edit" or edit_intent != "recreate":
        return prompt
    return (
        "Create a completely new depiction using the supplied reference images. "
        "Use each reference for the role assigned in the user's instruction. "
        "Preserve the recognizable identities and distinguishing design features of referenced subjects. "
        "Reconstruct those subjects in the requested visual style, with the requested pose, "
        "action, materials, lighting, composition and setting. Treat the original rendering "
        "style, pose and background as changeable. Any explicit preservation requirements "
        "in the user's instruction take priority. User instruction: " + prompt
    )


def configure_workflow(workflow, prompt, quality, enhance_prompt, seed=None, *,
                       qwen_sampler="default", qwen_scheduler="default", qwen_text_encoder="bf16"):
    """After upload placeholders have been expanded and unused slots removed."""
    validate_qwen21_selection("qwen21", qwen_sampler, qwen_scheduler, qwen_text_encoder)
    result = copy.deepcopy(workflow)
    encoder = result.get("5", {})
    if encoder.get("class_type") != "TextEncodeQwenImage21":
        raise RuntimeError("Install the Qwen 2.1 workflows before using these generation options")
    if result.get("2", {}).get("class_type") != "CLIPLoader" or result.get("6", {}).get("class_type") != "KSampler":
        raise RuntimeError("Install the Qwen 2.1 workflows before selecting a sampler or text encoder")
    result["2"]["inputs"]["clip_name"] = QWEN21_TEXT_ENCODERS[qwen_text_encoder]
    if qwen_sampler != "default":
        result["6"]["inputs"]["sampler_name"] = qwen_sampler
    if qwen_scheduler != "default":
        result["6"]["inputs"]["scheduler"] = qwen_scheduler
    resolution = 2048 if quality == "high" else 1024
    encoder["inputs"]["resolution"] = resolution
    if seed is not None:
        result["6"]["inputs"]["seed"] = seed
        if "10" in result:
            result["10"]["inputs"]["sampling_mode.seed"] = seed
    if not enhance_prompt:
        encoder["inputs"]["prompt"] = prompt
        for node_id in ("9", "10", "11", "12"):
            result.pop(node_id, None)
    return result


def qwen21_controls():
    return '''<fieldset id="qwen21-options"><legend>Qwen Image 2.1 settings</legend>
<label>Sampler<select name="qwen_sampler"><option value="default">Default — from workflow</option>
<option value="euler">Euler</option><option value="er_sde">ER-SDE</option></select></label>
<label>Scheduler<select name="qwen_scheduler"><option value="default">Default — from workflow</option>
<option value="simple">Simple</option><option value="beta">Beta</option></select></label>
<label>Text encoder<select name="qwen_text_encoder"><option value="bf16">Qwen3-VL 8B — BF16</option>
<option value="int8_convrot">Qwen3-VL 8B — INT8 ConvRot</option></select></label>
<p class="muted">INT8 ConvRot needs the additional encoder download. Prompt expansion and Qwen 2.1 LoRAs work with either encoder.</p>
</fieldset><script>document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('generation-form');
  if (!form) return;
  const fieldset = document.getElementById('qwen21-options');
  const update = () => { const enabled = form.elements.model.value === 'qwen21';
    fieldset.hidden = !enabled; fieldset.disabled = !enabled; };
  form.elements.model.addEventListener('change', update); update();
});</script>'''
