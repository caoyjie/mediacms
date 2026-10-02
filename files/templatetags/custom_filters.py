import json

from django import template
from django.utils.safestring import mark_safe

from files.frontend_translations import translate_string

register = template.Library()


@register.filter
def json_string(value):
    """Serialize a string for JSON-LD without allowing an HTML script breakout."""
    encoded = json.dumps(value)
    return mark_safe(encoded.translate({ord('<'): '\\u003C', ord('>'): '\\u003E', ord('&'): '\\u0026'}))


@register.filter
def custom_translate(string, lang_code):
    return translate_string(lang_code, string)
