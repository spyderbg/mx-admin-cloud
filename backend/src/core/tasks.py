import os
from src.core.config import settings

# Keys whose values must not be exposed in API responses
_SENSITIVE_KEYS = {"SECRET_KEY", "OPENAI_API_KEY"}


def _env_from_settings():
    """Build a dict of settings (env vars used in project) with sensitive values masked."""
    env = {}
    for name, value in settings.model_dump().items():
        if name in _SENSITIVE_KEYS:
            env[name] = "***" if value else "<unset>"
        else:
            env[name] = value
    return env

def ping():
    return {"message": f"Pong from KiddoWords backend!"}

def status():
    return {
        "status": "All services operational.",
        "env": _env_from_settings(),
    }

def operation(operation_name: str):
    return {"operation": operation_name, "result": "success"}

def list_files(depth: int = 1):
    directory = settings.CONTENT_DIR
    if not os.path.exists(directory):
        return {"error": f"Directory '{directory}' does not exist"}
    try:
        files = os.listdir(directory)
        return {"directory": directory, "files": files, "depth": depth}
    except Exception as e:
        return {"error": str(e)}
