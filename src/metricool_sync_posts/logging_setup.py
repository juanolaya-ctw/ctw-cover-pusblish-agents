import logging
import re
import sys

# httpx INFO lines print the full request URL. Slack webhooks and token query
# params must not land in the log even if some other logger includes them.
_SLACK_WEBHOOK = re.compile(r"https://hooks\.slack\.com/services/\S+", re.IGNORECASE)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|user_?token|access_token|auth|api_key|apikey|webhook|secret)=)"
    r"[^&\s\"']+"
)


def redact_secrets(text: str) -> str:
    """Replace Slack webhook URLs and token query parameters."""
    cleaned = _SLACK_WEBHOOK.sub("https://hooks.slack.com/services/REDACTED", text)
    return _QUERY_SECRET.sub(r"\1REDACTED", cleaned)


class RedactSecretsFilter(logging.Filter):
    """Rewrite the formatted message so every handler sees the redacted text."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        redacted = redact_secrets(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    redact = RedactSecretsFilter()
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, RedactSecretsFilter) for item in handler.filters):
            handler.addFilter(redact)
    # Stay quiet even when the root level is DEBUG. These loggers print URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
