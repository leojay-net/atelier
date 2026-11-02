"""The agent layer (docs/04-agent-layer.md): a plain-language request in, an executed and
checked spec out, at the fewest tokens.

Everything media-specific lives in the engine's ops, presets and recipes; this package only
drives the Messages API loop around them. Importing it does not import the `anthropic` SDK
(see `client.py`), so engine workers and the fast test suite stay light.
"""
