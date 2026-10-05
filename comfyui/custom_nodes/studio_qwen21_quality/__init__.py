"""Keep enhancer output usable as a prompt, with clear errors on truncation."""
import json
import re


def clean_prompt(text):
    text = text.strip()
    if text.startswith("<think>"):
        if "</think>" not in text:
            raise RuntimeError("Prompt expansion exhausted its token budget. Disable automatic expansion and retry.")
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            text = parsed["rewritten_prompt"]
            if not isinstance(text, str):
                raise ValueError("Expected a string")
            text = text.strip()
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Prompt expansion returned incomplete data. Disable automatic expansion and retry.") from exc
    if not text:
        raise RuntimeError("Prompt expansion produced no final description. Disable automatic expansion and retry.")
    return text


class StudioQwen21PromptResult:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"text": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING",)
    FUNCTION = "parse"
    CATEGORY = "text/qwen"

    def parse(self, text):
        return (clean_prompt(text),)


NODE_CLASS_MAPPINGS = {"StudioQwen21PromptResult": StudioQwen21PromptResult}
NODE_DISPLAY_NAME_MAPPINGS = {"StudioQwen21PromptResult": "Qwen 2.1 Prompt Result"}
