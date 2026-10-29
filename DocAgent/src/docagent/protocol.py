"""Conservative envelope repair: never change JSON values or synthesize missing content."""
import json

def normalize_arguments(raw):
    if not isinstance(raw,str):return raw,None
    text=raw.lstrip()
    try:
        value,end=json.JSONDecoder().raw_decode(text)
    except (ValueError,TypeError):
        return raw,None
    tail=text[end:].strip()
    if isinstance(value,dict) and tail in {'}','}}'}:
        # A complete first object followed only by surplus closing braces is unambiguous.
        # Never accept extra objects, prose, truncated objects, or modify a quote/number.
        return text[:end],{'method':'surplus_terminal_braces','removed_characters':len(tail)}
    return raw,None
