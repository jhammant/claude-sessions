"""Deterministic local replacement for paid extraction in integration tests."""
import json
import sys

text = sys.stdin.read()
assert 'backpressure' in text
print(json.dumps({'events': [{'type': 'asked_basic_question', 'concept': 'backpressure',
                             'area': 'web-backend', 'note': 'fixture evidence'}], 'recall': []}))
