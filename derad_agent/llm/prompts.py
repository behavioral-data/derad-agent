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


RESPONSE_OUTPUT_TEMPLATE = """You are responding to a claim. Below are independent pieces of evidence: real statements written by people about tweets related to this claim. Read them, weigh the evidence they present, and form your own response.

CLAIM:
{statement}

EVIDENCE_NOTES_JSON:
{evidence_notes_json}

Each note has a "note" field (the text), a "note_id", a "tweet_id", and optional "evidence_links".

Based on what these notes say, write a direct response to the claim and list the key reasons supporting your response.

Produce JSON only with this exact schema:
{{
  "response": "<direct 3-5 sentence response to the claim, written as if explaining to a friend>",
  "reasons": [
    {{
      "reason": "<concise reason drawn from a specific note>",
      "note_id": "<note_id from EVIDENCE_NOTES_JSON>",
      "tweet_id": "<tweet_id from EVIDENCE_NOTES_JSON>",
      "evidence_links": ["<source URL from the note, if available>"]
    }}
  ]
}}

Rules:
- Read the actual content of each note and reason over what it says. Do NOT rely on any metadata or labels — only the note text matters.
- Respond directly to the claim. Do NOT describe the dataset, the retrieval process, or the distribution of notes.
- Do NOT cite percentages, counts, ratios, or any statistical language.
- Ground every reason in a specific note. Do not invent note_id or tweet_id values.
- Return 3-5 reasons.
- Include evidence_links only when source URLs appear in the note.
- If the evidence is mixed or unclear, say so plainly without citing numbers.
- If there are no evidence notes, return an empty reasons list and say the evidence is insufficient.
"""


def get_planner_prompt():
    """Get the planner prompt template."""
    return PromptTemplate(input_variables=["statement"], template=PLANNER_TEMPLATE)


def get_response_output_prompt():
    """Get the claim-response output prompt template."""
    return PromptTemplate(
        input_variables=["statement", "evidence_notes_json"],
        template=RESPONSE_OUTPUT_TEMPLATE,
    )


__all__ = [
    "PLANNER_TEMPLATE",
    "get_planner_prompt",
    "RESPONSE_OUTPUT_TEMPLATE",
    "get_response_output_prompt",
]
