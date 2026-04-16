"""Output formatting: JSON, auth error, and request error messages."""
import json
import re
import sys
import textwrap
from http import HTTPStatus
import httpx
import requests
from typing import Optional, Dict

# Box inner width (chars between the two vertical bars)
_BOX_WIDTH = 70

# HTTP status code -> description (RFC 7231 and common extensions)
_KNOWN_HTTP_STATUS_CODES = (
    400, 401, 403, 404, 405, 408, 409, 422, 429, 500, 502, 503, 504,
)
_STATUS_PHRASE_OVERRIDES: dict[int, str] = {
    # Keep legacy CLI wording unchanged while using stdlib status metadata.
    422: "Unprocessable Entity",
}
HTTP_STATUS_DESCRIPTIONS: dict[int, str] = {
    code: _STATUS_PHRASE_OVERRIDES.get(code, HTTPStatus(code).phrase)
    for code in _KNOWN_HTTP_STATUS_CODES
}


def parse_http_error(error_text: str) -> Dict[str, Optional[str]]:
    """
    Parse an HTTP error string into structured components.

    Returns:
        dict with keys:
        - message
        - status_code
        - status_text
        - url
        - docs_url
    """
    result = {
        "message": None,
        "status_code": None,
        "status_text": None,
        "url": None,
        "docs_url": None,
    }

    # Main error line
    main_pattern = re.search(
        r"^(?P<message>.+?): Client error '(?P<status_code>\d+)\s+(?P<status_text>[^']+)' for url '(?P<url>[^']+)'",
        error_text,
        re.MULTILINE,
    )

    if main_pattern:
        result.update(main_pattern.groupdict())

    # Docs URL
    docs_pattern = re.search(
        r"For more information check:\s*(?P<docs_url>\S+)",
        error_text,
    )

    if docs_pattern:
        result["docs_url"] = docs_pattern.group("docs_url")

    return result


def _box_line(content: str) -> str:
    """One box row with right-side closing; exactly _BOX_WIDTH chars between the two │."""
    inner = (content + " " * _BOX_WIDTH)[:_BOX_WIDTH]
    return "  │" + inner + "│"


def _wrapped_box_lines(
        content: str,
        initial_indent: str = "",
        subsequent_indent: str = "",
) -> list[str]:
    """Wrap content into box-safe lines that fit the inner width."""
    wrapped_lines: list[str] = []
    segments = content.splitlines() or [""]
    for segment in segments:
        wrapped = textwrap.wrap(
            segment,
            width=_BOX_WIDTH,
            initial_indent=initial_indent,
            subsequent_indent=subsequent_indent,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        )
        if wrapped:
            wrapped_lines.extend(wrapped)
        else:
            wrapped_lines.append("")
    return wrapped_lines


def _border_top() -> str:
    """Return the top border line of a box."""
    return "  ┌" + "─" * _BOX_WIDTH + "┐"


def _border_mid() -> str:
    """Return a mid-section separator line of a box."""
    return "  ├" + "─" * _BOX_WIDTH + "┤"


def _border_bot() -> str:
    """Return the bottom border line of a box."""
    return "  └" + "─" * _BOX_WIDTH + "┘"


def format_json(data: object) -> str:
    """Pretty-print JSON for API responses."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def print_json(data: object, file: object = None) -> None:
    """Print data as formatted JSON to stdout (or file)."""
    print(format_json(data), file=file or sys.stdout)


def print_json_message(message: object, file: object = None) -> None:
    """Print a message from JSON format to stdout (or file)."""
    if isinstance(message, dict):
        message = message.get("message")
        print(message, file=file or sys.stdout)


def _box(lines: list[str], file: object = sys.stderr) -> None:
    """Print each line to *file* (stderr by default)."""
    for line in lines:
        print(line, file=file)


def print_error(title: str, message: str) -> None:
    """Print a formatted generic error to stderr."""
    _box([
        "",
        _border_top(),
        _box_line(title),
        _border_mid(),
        _box_line(message),
        _border_bot(),
        "",
    ])


def print_http_auto_failed(url: str, details: str) -> None:
    """Print a clearly formatted auth-failed error to stderr."""
    parsed_error = parse_http_error(details)
    details_lines = []

    for key, value in parsed_error.items():
        if value is not None:
            details_lines.extend(
                _box_line(line)
                for line in _wrapped_box_lines(
                    f"'{key}': '{value}',",
                    initial_indent="  ",
                    subsequent_indent="  ",
                )
            )

    if len(details_lines) == 0:
        details_lines.extend(
            _box_line(line)
            for line in _wrapped_box_lines(
                details,
                initial_indent="  ",
                subsequent_indent="  ",
            )
        )

        # print(f"details: {parsed_error}")

    _box([
        "",
        _border_top(),
        _box_line("Authentication failed"),
        _border_mid(),
        _box_line("The CLI could not reach the backend HTTP server."),
        _box_line(""),
        _box_line(f"Base URL:     {url}"),
        _border_mid(),
        _box_line("Details:"),
        *details_lines,
        _border_bot(),
        ""
    ])


def print_uds_auth_failed(socket_path: str, details: str) -> None:
    """Print a clearly formatted auth-failed error to stderr."""
    parsed_error = parse_http_error(details)
    details_lines = []

    for key, value in parsed_error.items():
        if value is not None:
            details_lines.append(_box_line(f" '{key}': '{value}',"))

    if len(details_lines) == 0:
        details_lines.append(_box_line(f" {details}"))

    body_lines = [
        "",
        _border_top(),
        _box_line("Authentication failed"),
        _border_mid(),
        _box_line("The CLI could not reach the backend CLI listener."),
        _box_line(""),
        _box_line(f"Socket:  {socket_path}"),
        _box_line(""),
        _box_line("Start the backend so the CLI socket is available:"),
        _box_line("  cd backend && ./uvicorn_start.sh"),
        _box_line(""),
        _box_line("(This starts both the public HTTP server and the CLI Unix socket.)"),
        _border_mid(),
    ]
    lowered = details.lower()
    if "111" in details or "connection refused" in lowered or "connect error" in lowered:
        body_lines.extend([
            _box_line("If the socket file exists but nothing is listening, remove it and"),
            _box_line("restart the backend, for example:"),
            _box_line(f"  rm -f {socket_path}"),
            _box_line("  cd backend && ./uvicorn_start.sh"),
            _border_mid(),
        ])
    body_lines.extend([
        _box_line("Details:"),
        *details_lines,
        _border_bot(),
        "",
    ])
    _box(body_lines)


def _format_response_detail(response: requests.Response) -> str:
    """Try to extract a short detail from the response body."""
    try:
        body = response.json()
        if isinstance(body, dict):
            if "detail" in body:
                d = body["detail"]
                if isinstance(d, list):
                    return "; ".join(
                        f"{x.get('loc', [])}: {x.get('msg', x)}" for x in d[:5]
                    )
                return str(d)
            if "message" in body:
                return str(body["message"])
        return response.text[:200].strip() or "(no body)"
    except Exception:
        return response.text[:200].strip() or "(no body)"


def print_request_error(exc: BaseException) -> None:
    """Print a formatted request-failed (or generic) error to stderr."""
    title = "Request failed"
    details = str(exc)
    url = ""
    status_code = ""
    body_detail = ""
    status_description = ""

    if isinstance(exc, requests.RequestException):
        if hasattr(exc, "response") and exc.response is not None:
            response: requests.Response = exc.response
            url = response.url or ""
            status_code = str(response.status_code)
            code = response.status_code
            status_description = HTTP_STATUS_DESCRIPTIONS.get(
                code, response.reason or "Unknown"
            )
            body_detail = _format_response_detail(response)
            if body_detail and body_detail != details:
                details = f"{response.status_code} {response.reason}; {body_detail}"
            else:
                details = f"{response.status_code} {response.reason} for url: {url}"
    elif isinstance(exc, httpx.HTTPError):
        if hasattr(exc, "response") and exc.response is not None:
            resp: httpx.Response = exc.response
            url = str(resp.url)
            status_code = str(resp.status_code)
            code = resp.status_code
            status_description = HTTP_STATUS_DESCRIPTIONS.get(
                code, resp.reason_phrase or "Unknown"
            )
            try:
                body = resp.json()
                if isinstance(body, dict) and "detail" in body:
                    body_detail = str(body["detail"])
                else:
                    body_detail = resp.text[:200].strip() or "(no body)"
            except (ValueError, TypeError, json.JSONDecodeError):
                body_detail = resp.text[:200].strip() or "(no body)"
            details = f"{resp.status_code} {resp.reason_phrase}; {body_detail}"
    else:
        print_error(title, details)
        return

    lines = [
        "",
        _border_top(),
        _box_line(title),
        _border_mid(),
    ]
    if status_code:
        lines.append(_box_line(f"Status:  {status_code} {status_description}"))
    if url:
        lines.append(_box_line(f"URL:     {url}"))
    lines.append(_border_mid())
    # Show detail (body_detail for HTTP errors, else the exception string)
    message = (body_detail if body_detail else details).strip()
    content_width = _BOX_WIDTH - 2  # for wrapped lines
    for part in message.split("\n")[:10]:
        while len(part) > content_width:
            lines.append(_box_line(part[:content_width]))
            part = part[content_width:].lstrip()
        if part:
            lines.append(_box_line(part))
    lines.extend([_border_bot(), ""])
    _box(lines)
