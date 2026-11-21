"""Conservative quarantine for obvious embedded assistant-control instructions."""

import re


def instruction_text(text):
    return bool(re.search(
        r"ignore\b.{0,70}\b(?:previous|prior|system|developer|all)\b.{0,30}\binstructions?\b|"
        r"(?:system|developer)\s*(?:prompt|message)\s*:|<\|(?:system|im_start)\|>|"
        r"(?:reveal|print|send|exfiltrate)\b.{0,50}\b(?:api key|password|secret|system prompt)\b|"
        r"(?:override|bypass|ignore)\b.{0,35}\b(?:privacy|permissions|access rules)\b",
        text, re.I | re.S))
