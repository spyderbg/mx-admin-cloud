"""Internal endpoints for in-memory and persisted service config management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile  # pylint: disable=import-error

from src.api.dependencies import get_config_file_store, get_registry_builder, require_internal
from src.api.internal.schemas.service_config import (
    AddConfigRequest,
    ApplyConfigRequest,
    ApplyConfigResponse,
    BatchToggleConfigurationsRequest,
    BatchToggleConfigurationsResponse,
    ConfigFileSummaryResponse,
    ConfigMutationResponse,
    PatchConfigRequest,
)
from src.core.utils import JsonObjectReadError, read_json_object
from src.services.config_file_store import (
    ConfigConflictError,
    ConfigNotFoundError,
    ConfigValidationError,
    InvalidConfigNameError,
    ServiceConfigFileStore,
    UnknownServiceError,
)
from src.services.service_registry import ServiceRegistryBuilder

router = APIRouter(prefix="/internal/services/configs", tags=["internal-service-configs"])


@router.post("/apply", response_model=ApplyConfigResponse)
async def apply_config_payload(
        body: ApplyConfigRequest,
        _user: dict = Depends(require_internal),
        builder: ServiceRegistryBuilder = Depends(get_registry_builder),
) -> ApplyConfigResponse:
    """Validate and apply one service config payload in memory."""
    result = builder.apply_runtime_payload(body.payload, source="<runtime-upload>")
    if not result.accepted:
        raise HTTPException(status_code=422, detail=result.error or "Config validation failed.")
    return ApplyConfigResponse(
        config_name=result.config_name,
        accepted=result.accepted,
        error=result.error,
    )


@router.post("/upload", response_model=ApplyConfigResponse)
async def upload_config_file(
        file: UploadFile = File(...),
        _user: dict = Depends(require_internal),
        builder: ServiceRegistryBuilder = Depends(get_registry_builder),
) -> ApplyConfigResponse:
    """Validate and apply uploaded config content in memory without persisting."""
    try:
        payload_raw = await file.read()
        payload = read_json_object(payload_raw)
    except JsonObjectReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source = f"<runtime-upload:{file.filename or 'unknown.json'}>"
    result = builder.apply_runtime_payload(payload, source=source)
    if not result.accepted:
        raise HTTPException(status_code=422, detail=result.error or "Config validation failed.")
    return ApplyConfigResponse(
        config_name=result.config_name,
        accepted=result.accepted,
        error=result.error,
    )


@router.get("/{service_name}", response_model=list[ConfigFileSummaryResponse])
def list_service_configs(
        service_name: str,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> list[ConfigFileSummaryResponse]:
    """List JSON config files for one service."""
    try:
        summaries = store.list_configs(service_name)
    except UnknownServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ConfigFileSummaryResponse(**summary.__dict__) for summary in summaries]


@router.post("/{service_name}", response_model=ConfigMutationResponse)
def add_service_config(
        service_name: str,
        body: AddConfigRequest,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> ConfigMutationResponse:
    """Create a new JSON config file for one service."""
    try:
        result = store.add_config(service_name=service_name, config_name=body.config_name, payload=body.payload)
    except UnknownServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (InvalidConfigNameError, ConfigValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigMutationResponse(**result.__dict__)


@router.delete("/{service_name}/{config_name}", response_model=ConfigMutationResponse)
def remove_service_config(
        service_name: str,
        config_name: str,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> ConfigMutationResponse:
    """Remove one JSON config file for one service."""
    try:
        result = store.remove_config(service_name=service_name, config_name=config_name)
    except (UnknownServiceError, ConfigNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidConfigNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigMutationResponse(**result.__dict__)


@router.patch("/{service_name}/{config_name}", response_model=ConfigMutationResponse)
def patch_service_config(
        service_name: str,
        config_name: str,
        body: PatchConfigRequest,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> ConfigMutationResponse:
    """Merge-patch one JSON config file for one service."""
    try:
        result = store.patch_config(
            service_name=service_name,
            config_name=config_name,
            patch=body.patch,
        )
    except (UnknownServiceError, ConfigNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (InvalidConfigNameError, ConfigValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigMutationResponse(**result.__dict__)


@router.post("/{service_name}/enable-configurations", response_model=BatchToggleConfigurationsResponse)
def enable_configurations(
        service_name: str,
        body: BatchToggleConfigurationsRequest,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> BatchToggleConfigurationsResponse:
    """Enable selected configs for one service."""
    return _toggle_configurations(service_name=service_name, config_names=body.config_names, enabled=True, store=store)


@router.post("/{service_name}/disable-configurations", response_model=BatchToggleConfigurationsResponse)
def disable_configurations(
        service_name: str,
        body: BatchToggleConfigurationsRequest,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> BatchToggleConfigurationsResponse:
    """Disable selected configs for one service."""
    return _toggle_configurations(
        service_name=service_name,
        config_names=body.config_names,
        enabled=False,
        store=store,
    )


@router.post("/{service_name}/enable-all-configurations", response_model=BatchToggleConfigurationsResponse)
def enable_all_configurations(
        service_name: str,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> BatchToggleConfigurationsResponse:
    """Enable all configs for one service."""
    return _toggle_all_configurations(service_name=service_name, enabled=True, store=store)


@router.post("/{service_name}/disable-all-configurations", response_model=BatchToggleConfigurationsResponse)
def disable_all_configurations(
        service_name: str,
        _user: dict = Depends(require_internal),
        store: ServiceConfigFileStore = Depends(get_config_file_store),
) -> BatchToggleConfigurationsResponse:
    """Disable all configs for one service."""
    return _toggle_all_configurations(service_name=service_name, enabled=False, store=store)


def _toggle_configurations(
        service_name: str,
        config_names: list[str],
        enabled: bool,
        store: ServiceConfigFileStore,
) -> BatchToggleConfigurationsResponse:
    try:
        results = store.set_configurations_enabled(
            service_name=service_name,
            config_names=config_names,
            enabled=enabled,
        )
    except UnknownServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BatchToggleConfigurationsResponse(
        service_name=service_name,
        enabled=enabled,
        results=[ConfigMutationResponse(**result.__dict__) for result in results],
    )


def _toggle_all_configurations(
        service_name: str,
        enabled: bool,
        store: ServiceConfigFileStore,
) -> BatchToggleConfigurationsResponse:
    try:
        results = store.set_all_configurations_enabled(service_name=service_name, enabled=enabled)
    except UnknownServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BatchToggleConfigurationsResponse(
        service_name=service_name,
        enabled=enabled,
        results=[ConfigMutationResponse(**result.__dict__) for result in results],
    )
