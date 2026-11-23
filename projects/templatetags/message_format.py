from django import template
from projects.message_content import rendered_message

register = template.Library()
register.filter('message_html', rendered_message)
