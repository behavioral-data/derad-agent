"""Step 1: Single-pass query generation (planning)."""

from typing import List, Optional

from derad_agent.llm.config import get_llm
from derad_agent.llm.prompts import get_planner_prompt
from derad_agent.shared.logging import RuntimeLogger
from derad_agent.shared.validation import validate_search_queries

from ._helpers import extract_text_from_response, parse_json_response


def step_1_generate_queries(
    statement: str,
    logger: RuntimeLogger,
    preset_queries: Optional[List[str]] = None,
) -> List[str]:
    """Generate 1-6 search queries from the input statement."""
    logger.log_step('planner', "Generating queries")

    if preset_queries:
        logger.log_info(f"Using {len(preset_queries)} preset queries (planner disabled)")
        return validate_search_queries(preset_queries, min_queries=1, max_queries=6)

    prompt = get_planner_prompt()
    input_vars = {"statement": statement}

    llm_instance = get_llm(
        temperature=None,
        max_tokens=2000,
        reasoning_effort="low",
        text_verbosity="medium",
    )
    chain = prompt | llm_instance

    logger.log_debug(f"Prompt length: {len(prompt.format_prompt(**input_vars).to_string())} chars")

    try:
        raw_output_obj = chain.invoke(input_vars)
        raw_output = extract_text_from_response(raw_output_obj)
    except Exception as e:
        logger.log_warning(f"Planner LLM call failed (content filter or API error): {e}")
        return [statement]

    logger.log_debug(f"Response received: {len(raw_output)} chars")

    try:
        parsed = parse_json_response(raw_output)
        queries = parsed.get("queries", [])
        if queries:
            queries = validate_search_queries(queries, min_queries=1, max_queries=6)
            logger.log_info(f"Generated {len(queries)} valid queries")
        else:
            logger.log_warning("No queries found in planner output (empty list), using fallback")
            queries = [statement]
    except Exception as e:
        logger.log_warning(f"Failed to parse JSON planner output: {e}")
        logger.log_warning(f"Raw planner output:\n{raw_output[:1000]}")
        queries = [statement]

    return queries


