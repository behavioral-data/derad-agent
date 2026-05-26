"""Web-based fact-checking pipeline (Stages 1-9 per agent_design.md).

The pipeline produces a `FrozenVerdict` object that the tone renderer reads
under a strict invariance contract: only `presentation_payload` and
`tone_neutral_justification` are exposed to the renderer.
"""
