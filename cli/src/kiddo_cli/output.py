"""Output formatting: JSON, auth error, and request error messages."""
import json
import re
import sys
import requests
from typing import Optional, Dict

# Box inner width (chars between the two vertical bars)
_BOX_WIDTH = 80

# HTTP status code -> description (RFC 7231 and common extensions)
HTTP_STATUS_DESCRIPTIONS: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
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


def _border_top() -> str:
    return "  ┌" + "─" * _BOX_WIDTH + "┐"


def _border_mid() -> str:
    return "  ├" + "─" * _BOX_WIDTH + "┤"


def _border_bot() -> str:
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
            details_lines.append(_box_line(f" '{key}': '{value}',"))

    if len(details_lines) == 0:
        details_lines.append(_box_line(f" {details}"))

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

    _box([
        "",
        _border_top(),
        _box_line("Authentication failed"),
        _border_mid(),
        _box_line("The CLI could not reach the backend CLI listener."),
        _box_line(""),
        _box_line(f"Socket:  {socket_path}"),
        _box_line(""),
        _box_line("Start the backend so the CLI socket is available:"),
        _box_line("  ./uvicorn_start.sh"),
        _box_line(""),
        _box_line("(This starts both the public HTTP server and the CLI Unix socket.)"),
        _border_mid(),
        _box_line("Details:"),
        *details_lines,
        _border_bot(),
        "",
    ])


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

    if not isinstance(exc, requests.RequestException):
        print_error(title, details)
        return

    status_description = ""
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
