"""Step 1: Factcheckability assessment + query generation (planning)."""

import os
from typing import List, Optional, Tuple

from derad_agent.llm.config import get_llm
from derad_agent.llm.prompts import get_planner_prompt
from derad_agent.shared.logging import RuntimeLogger
from derad_agent.shared.validation import validate_search_queries

from ._helpers import extract_text_from_response, parse_json_response

_PLANNER_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT_PLANNER", "claude-sonnet-4-6")
_PLANNER_PROVIDER = os.getenv("DERAD_PLANNER_PROVIDER", "claude")


def step_1_generate_queries(
    statement: str,
    logger: RuntimeLogger,
    preset_queries: Optional[List[str]] = None,
) -> Tuple[bool, List[str]]:
    """Assess factcheckability and generate 1-6 search queries.

    Returns ``(factcheckable, queries)``. If ``factcheckable`` is False, the
    caller should skip retrieval and compose a no-factcheck reply instead.
    """
    logger.log_step('planner', "Assessing claim + generating queries")

    if preset_queries:
        logger.log_info(f"Using {len(preset_queries)} preset queries (planner disabled)")
        return True, validate_search_queries(preset_queries, min_queries=1, max_queries=6)

    prompt = get_planner_prompt()
    input_vars = {"statement": statement}

    llm_instance = get_llm(
        temperature=None,
        max_tokens=2000,
        deployment=_PLANNER_DEPLOYMENT,
        provider=_PLANNER_PROVIDER,
    )
    chain = prompt | llm_instance

    logger.log_debug(f"Prompt length: {len(prompt.format_prompt(**input_vars).to_string())} chars")

    try:
        raw_output_obj = chain.invoke(input_vars)
        raw_output = extract_text_from_response(raw_output_obj)
    except Exception as e:
        logger.log_warning(f"Planner LLM call failed (content filter or API error): {e}")
        return False, []

    logger.log_debug(f"Response received: {len(raw_output)} chars")

    try:
        parsed = parse_json_response(raw_output)
        factcheckable = bool(parsed.get("factcheckable", False))
        if not factcheckable:
            logger.log_info("Planner: tweet is not factcheckable — skipping retrieval")
            return False, []
        queries = parsed.get("queries", [])
        if queries:
            queries = validate_search_queries(queries, min_queries=1, max_queries=6)
            logger.log_info(f"Generated {len(queries)} valid queries")
        else:
            logger.log_warning("No queries in planner output, using statement as fallback")
            queries = [statement]
    except Exception as e:
        logger.log_warning(f"Failed to parse JSON planner output: {e}")
        logger.log_warning(f"Raw planner output:\n{raw_output[:1000]}")
        queries = []
        factcheckable = False

    return factcheckable, queries


