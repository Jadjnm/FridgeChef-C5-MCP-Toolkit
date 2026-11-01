"""Provider wiring checks in fresh processes, with dotenv disabled and fake credentials."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("provider, expected", [
    ("bedrock", "LitellmModel"), ("openai", "OpenAIChatCompletionsModel"),
    ("openrouter", "OpenAIChatCompletionsModel"),
])
def test_selected_provider_builds_the_right_model_without_network(provider, expected):
    code = """
import os
from unittest.mock import patch
with patch('dotenv.load_dotenv'):
    import agent
configured = agent.make_agent(object())
assert type(configured.model).__name__ == os.environ['EXPECTED_MODEL_TYPE']
assert agent.model_id() == os.environ['EXPECTED_MODEL_ID']
if agent.model_provider() in {'openai', 'openrouter'}:
    key_name = 'OPENROUTER_API_KEY' if agent.model_provider() == 'openrouter' else 'OPENAI_API_KEY'
    del os.environ[key_name]
    try:
        agent.make_agent(object())
    except ValueError as exc:
        assert key_name in str(exc)
    else:
        raise AssertionError('A missing selected-provider key must not trigger fallback')
os.environ['LLM_PROVIDER'] = 'unsupported'
try:
    agent.model_provider()
except ValueError:
    pass
else:
    raise AssertionError('Unsupported providers must fail clearly')
"""
    env = {k: os.environ[k] for k in (
        "SystemRoot", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "USERPROFILE", "PYTHONUTF8",
    ) if k in os.environ}
    env.update(LLM_PROVIDER=provider, OPENAI_API_KEY="fake-offline-key", OPENAI_MODEL="openai-test",
               OPENROUTER_API_KEY="fake-router-key", OPENROUTER_MODEL="openrouter-test",
               BEDROCK_MODEL_ID="bedrock-test", EXPECTED_MODEL_TYPE=expected,
               EXPECTED_MODEL_ID=f"{provider}-test")
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
