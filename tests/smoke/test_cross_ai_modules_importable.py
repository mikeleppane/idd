"""Smoke import test for the cross_ai package."""

from tools.cross_ai import config, cost, detect, disclosure, parse, prompt


def test_modules_expose_documented_public_names() -> None:
    assert callable(config.load_config)
    assert callable(detect.detect_clis)
    assert callable(cost.estimate_tokens)
    assert callable(prompt.build_prompt)
    assert callable(disclosure.build_disclosure)
    assert callable(parse.parse_response)
