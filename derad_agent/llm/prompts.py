"""Prompt templates for query planning and final landscape output."""

try:
    from langchain_core.prompts import PromptTemplate
except ImportError:
    try:
        from langchain.prompts import PromptTemplate
    except ImportError:  # lightweight fallback for offline/testing scenarios
        class PromptTemplate:  # type: ignore
            def __init__(self, input_variables, template):
                self.input_variables = input_variables
                self.template = template

            def format(self, **kwargs):
                return self.template.format(**kwargs)


PLANNER_TEMPLATE = """Your task is to understand a user's statement or claim and break it down into 3-4 focused search queries.
These queries will retrieve relevant information from a vector index of Community Notes summaries grouped by tweet clusters.

You must output your response in JSON format.

STATEMENT: {statement}

Output format:
{{
  "queries": ["query 1", "query 2", "query 3"]
}}"""


LANDSCAPE_OUTPUT_TEMPLATE = """You are generating the final output for a statement-focused Community Notes retrieval pipeline.
Use ONLY the provided evidence.

STATEMENT:
{statement}

LANDSCAPE_STATS_JSON (distribution, frequencies, quantiles, cluster stats):
{landscape_stats_json}

SPECTRUM_REPRESENTATIVE_NOTES_JSON (up to 20 notes spanning misleading, mixed, and not-misleading regions):
{top_points_json}

Produce JSON only with this exact schema:
{{
  "landscape_summary": "<4-6 sentence plain-language overview of what the retrieved landscape looks like around the statement>",
  "key_reasons": [
    {{
      "reason": "<concise reason grounded in retrieved notes>",
      "bucket": "misleading|not_misleading|mixed_unclear",
      "note_id": "<note id from TOP_RETRIEVED_NOTES_JSON>",
      "tweet_id": "<tweet id from TOP_RETRIEVED_NOTES_JSON>",
      "evidence_links": ["<optional source URL 1>", "<optional source URL 2>"]
    }}
  ]
}}

Rules:
- Ground every reason in SPECTRUM_REPRESENTATIVE_NOTES_JSON.
- Do not invent note_id or tweet_id values.
- Return 3-5 key reasons.
- Keep language neutral and evidence-driven.
- If evidence is sparse, explicitly say that in landscape_summary.
- Use LANDSCAPE_STATS_JSON to describe dominant patterns (frequency/distribution), not just individual notes.
- Include evidence_links when source URLs are present in SPECTRUM_REPRESENTATIVE_NOTES_JSON.
- If there are no representative notes, return `"key_reasons": []`.
"""


def get_planner_prompt():
    """Get the planner prompt template."""
    return PromptTemplate(input_variables=["statement"], template=PLANNER_TEMPLATE)


def get_landscape_output_prompt():
    """Get the final landscape output prompt template."""
    return PromptTemplate(
        input_variables=["statement", "landscape_stats_json", "top_points_json"],
        template=LANDSCAPE_OUTPUT_TEMPLATE,
    )


__all__ = [
    "PLANNER_TEMPLATE",
    "get_planner_prompt",
    "LANDSCAPE_OUTPUT_TEMPLATE",
    "get_landscape_output_prompt",
]
