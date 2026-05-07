"""
Shared helper functions for pipeline steps.

- LLM response extraction (Chat Completions + GPT-5 Responses API)
- JSON parsing with markdown code-block handling
- Diagnostic logging for refusals / empty outputs
"""

import ast
import re
import json
from typing import Any, Dict

# -- Pre-compiled patterns ------------------------------------------------

_RE_JSON_CODEBLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


# -- Public helpers -------------------------------------------------------

def extract_text_from_response(raw_output_obj: Any) -> str:
    """Extract text content from an LLM response object.

    Handles both the Chat Completions API and GPT-5 Responses API formats.
    """
    if hasattr(raw_output_obj, 'content'):
        content = raw_output_obj.content
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get('type')
                    if block_type == 'reasoning':
                        continue
                    if block_type == 'text' and 'text' in block:
                        text_parts.append(block['text'])
                elif hasattr(block, 'type') and getattr(block, 'type') == 'reasoning':
                    continue
                elif hasattr(block, 'text'):
                    text_parts.append(block.text)
            return "\n".join(text_parts) if text_parts else str(content)
        else:
            return content
    else:
        return str(raw_output_obj)


def parse_json_response(text: str) -> Dict[str, Any]:
    """Parse JSON from an LLM response, handling markdown code blocks."""
    text = text.strip()
    match = _RE_JSON_CODEBLOCK.search(text)
    if match:
        text = match.group(1)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        # Last resort: handle Python-dict-style output (single quotes)
        candidate = text[start:end + 1] if start != -1 and end != -1 else text
        try:
            result = ast.literal_eval(candidate)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        raise ValueError(f"Could not parse JSON from response: {text[:100]}...")
