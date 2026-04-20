"""Internal endpoints for in-memory and persisted service config management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile  # pylint: disable=import-error

from src.api.dependencies import get_configuration_service, get_registry_builder, require_internal
from src.core.utils import JsonObjectReadError, read_json_object
from src.services.configuration import (
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    ConfigurationService,
    ConfigurationValidationError,
    InvalidConfigurationNameError,
    UnknownConfigurationServiceError,
)
from src.services.configuration.schemas import (
    AddConfigurationRequest,
    ApplyConfigurationRequest,
    ApplyConfigurationResponse,
    BatchToggleConfigurationsRequest,
    BatchToggleConfigurationsResponse,
    ConfigurationFileSummaryResponse,
    ConfigurationMutationResponse,
    PatchConfigurationRequest,
)
from src.services.service_registry import ServiceRegistryBuilder

CONFIG_APPLY = "/apply"
CONFIG_UPLOAD = "/upload"
CONFIG_SERVICE = "/{service_name}"
CONFIG_SERVICE_ITEM = "/{service_name}/{config_name}"
CONFIG_ENABLE_CONFIGURATIONS = "/{service_name}/enable-configurations"
CONFIG_DISABLE_CONFIGURATIONS = "/{service_name}/disable-configurations"
CONFIG_ENABLE_ALL_CONFIGURATIONS = "/{service_name}/enable-all-configurations"
CONFIG_DISABLE_ALL_CONFIGURATIONS = "/{service_name}/disable-all-configurations"

router = APIRouter(prefix="/internal/configurations", tags=["internal-configuration"])


@router.post(CONFIG_APPLY, response_model=ApplyConfigurationResponse)
async def apply_config_payload(
        body: ApplyConfigurationRequest,
        _user: dict = Depends(require_internal),
        builder: ServiceRegistryBuilder = Depends(get_registry_builder),
) -> ApplyConfigurationResponse:
    """Validate and apply one service config payload in memory."""
    result = builder.apply_runtime_payload(body.payload, source="<runtime-upload>")
    if not result.accepted:
        raise HTTPException(status_code=422, detail=result.error or "Config validation failed.")
    return ApplyConfigurationResponse(
        config_name=result.config_name,
        accepted=result.accepted,
        error=result.error,
    )


@router.post(CONFIG_UPLOAD, response_model=ApplyConfigurationResponse)
async def upload_config_file(
        file: UploadFile = File(...),
        _user: dict = Depends(require_internal),
        builder: ServiceRegistryBuilder = Depends(get_registry_builder),
) -> ApplyConfigurationResponse:
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
    return ApplyConfigurationResponse(
        config_name=result.config_name,
        accepted=result.accepted,
        error=result.error,
    )


@router.get(CONFIG_SERVICE, response_model=list[ConfigurationFileSummaryResponse])
def list_service_configs(
        service_name: str,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> list[ConfigurationFileSummaryResponse]:
    """List JSON config files for one service."""
    try:
        summaries = configuration_service.list_configurations(service_name)
    except UnknownConfigurationServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ConfigurationFileSummaryResponse(**summary.__dict__) for summary in summaries]


@router.post(CONFIG_SERVICE, response_model=ConfigurationMutationResponse)
def add_service_config(
        service_name: str,
        body: AddConfigurationRequest,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> ConfigurationMutationResponse:
    """Create a new JSON config file for one service."""
    try:
        result = configuration_service.add_configuration(
            service_name=service_name,
            config_name=body.config_name,
            payload=body.payload,
        )
    except UnknownConfigurationServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (InvalidConfigurationNameError, ConfigurationValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigurationMutationResponse(**result.__dict__)


@router.delete(CONFIG_SERVICE_ITEM, response_model=ConfigurationMutationResponse)
def remove_service_config(
        service_name: str,
        config_name: str,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> ConfigurationMutationResponse:
    """Remove one JSON config file for one service."""
    try:
        result = configuration_service.remove_configuration(service_name=service_name, config_name=config_name)
    except (UnknownConfigurationServiceError, ConfigurationNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidConfigurationNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigurationMutationResponse(**result.__dict__)


@router.patch(CONFIG_SERVICE_ITEM, response_model=ConfigurationMutationResponse)
def patch_service_config(
        service_name: str,
        config_name: str,
        body: PatchConfigurationRequest,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> ConfigurationMutationResponse:
    """Merge-patch one JSON config file for one service."""
    try:
        result = configuration_service.patch_configuration(
            service_name=service_name,
            config_name=config_name,
            patch=body.patch,
        )
    except (UnknownConfigurationServiceError, ConfigurationNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (InvalidConfigurationNameError, ConfigurationValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigurationMutationResponse(**result.__dict__)


@router.post(CONFIG_ENABLE_CONFIGURATIONS, response_model=BatchToggleConfigurationsResponse)
def enable_configurations(
        service_name: str,
        body: BatchToggleConfigurationsRequest,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> BatchToggleConfigurationsResponse:
    """Enable selected configs for one service."""
    return _toggle_configurations(
        service_name=service_name,
        config_names=body.config_names,
        enabled=True,
        configuration_service=configuration_service,
    )


@router.post(CONFIG_DISABLE_CONFIGURATIONS, response_model=BatchToggleConfigurationsResponse)
def disable_configurations(
        service_name: str,
        body: BatchToggleConfigurationsRequest,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> BatchToggleConfigurationsResponse:
    """Disable selected configs for one service."""
    return _toggle_configurations(
        service_name=service_name,
        config_names=body.config_names,
        enabled=False,
        configuration_service=configuration_service,
    )


@router.post(CONFIG_ENABLE_ALL_CONFIGURATIONS, response_model=BatchToggleConfigurationsResponse)
def enable_all_configurations(
        service_name: str,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> BatchToggleConfigurationsResponse:
    """Enable all configs for one service."""
    return _toggle_all_configurations(
        service_name=service_name,
        enabled=True,
        configuration_service=configuration_service,
    )


@router.post(CONFIG_DISABLE_ALL_CONFIGURATIONS, response_model=BatchToggleConfigurationsResponse)
def disable_all_configurations(
        service_name: str,
        _user: dict = Depends(require_internal),
        configuration_service: ConfigurationService = Depends(get_configuration_service),
) -> BatchToggleConfigurationsResponse:
    """Disable all configs for one service."""
    return _toggle_all_configurations(
        service_name=service_name,
        enabled=False,
        configuration_service=configuration_service,
    )


def _toggle_configurations(
        service_name: str,
        config_names: list[str],
        enabled: bool,
        configuration_service: ConfigurationService,
) -> BatchToggleConfigurationsResponse:
    try:
        if enabled:
            results = configuration_service.enable_configurations(service_name, config_names)
        else:
            results = configuration_service.disable_configurations(service_name, config_names)
    except UnknownConfigurationServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BatchToggleConfigurationsResponse(
        service_name=service_name,
        enabled=enabled,
        results=[ConfigurationMutationResponse(**result.__dict__) for result in results],
    )


def _toggle_all_configurations(
        service_name: str,
        enabled: bool,
        configuration_service: ConfigurationService,
) -> BatchToggleConfigurationsResponse:
    try:
        if enabled:
            results = configuration_service.enable_all_configurations(service_name)
        else:
            results = configuration_service.disable_all_configurations(service_name)
    except UnknownConfigurationServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return BatchToggleConfigurationsResponse(
        service_name=service_name,
        enabled=enabled,
        results=[ConfigurationMutationResponse(**result.__dict__) for result in results],
    )
