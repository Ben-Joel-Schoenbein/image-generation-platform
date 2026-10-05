"""Request options for the Qwen Image 2.1 API workflows."""
import copy


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


def configure_workflow(workflow, prompt, quality, enhance_prompt, seed=None):
    """After upload placeholders have been expanded and unused slots removed."""
    result = copy.deepcopy(workflow)
    encoder = result.get("5", {})
    if encoder.get("class_type") != "TextEncodeQwenImage21":
        raise RuntimeError("Install the Qwen 2.1 workflows before using these generation options")
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
