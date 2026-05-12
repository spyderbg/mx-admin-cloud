"""Internal configuration endpoints

Use for in-memory and persisted service config management.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status  # pylint: disable=import-error

from src.api.dependencies import get_registry, get_registry_builder, require_internal
from src.api.service_resolver import get_typed_service
from src.core.utils import JsonObjectReadError, read_json_object
from src.services import configuration
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
from src.services.service_registry import ServiceRegistry
from src.services.service_registry import ServiceRegistryBuilder

router = APIRouter(prefix="/internal/configurations", tags=["internal-configurations"])

CONFIG_APPLY = "/apply"
CONFIG_UPLOAD = "/upload"
CONFIG_SERVICE = "/{service_name}"
CONFIG_SERVICE_ITEM = "/{service_name}/{config_name}"
CONFIG_ENABLE_CONFIGURATIONS = "/{service_name}/enable-configurations"
CONFIG_DISABLE_CONFIGURATIONS = "/{service_name}/disable-configurations"
CONFIG_ENABLE_ALL_CONFIGURATIONS = "/{service_name}/enable-all-configurations"
CONFIG_DISABLE_ALL_CONFIGURATIONS = "/{service_name}/disable-all-configurations"


@router.post(
    CONFIG_APPLY,
    response_model=ApplyConfigurationResponse,
    status_code=status.HTTP_200_OK,
    summary="Apply a runtime configuration payload",
    description=(
            "Validate a JSON configuration payload and apply it in memory through the "
            "service registry builder. The payload is not persisted to disk. "
            "Returns the resolved 'config_name' and whether the payload was accepted; "
            "rejected payloads respond with HTTP 422 and a validation error."
    ),
)
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


@router.post(
    CONFIG_UPLOAD,
    response_model=ApplyConfigurationResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload a runtime configuration file",
    description=(
            "Read a JSON configuration file from the multipart upload, validate it, "
            "and apply it in memory through the service registry builder. "
            "The uploaded content is not persisted to disk. "
            "Malformed JSON or invalid configurations respond with HTTP 422."
    ),
)
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


@router.get(
    CONFIG_SERVICE,
    response_model=list[ConfigurationFileSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="List configuration files for a service",
    description=(
            "Return summaries of every JSON configuration file registered under "
            "'service_name'. Each entry reports the resolved config name, filename, "
            "provider, enabled/priority metadata, and JSON validity. "
            "Unknown service names respond with HTTP 404."
    ),
)
def list_service_configs(
        service_name: str,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> list[ConfigurationFileSummaryResponse]:
    """List JSON config files for one service."""
    try:
        summaries = service.list_configurations(service_name)
    except UnknownConfigurationServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ConfigurationFileSummaryResponse(**summary.__dict__) for summary in summaries]


@router.post(
    CONFIG_SERVICE,
    response_model=ConfigurationMutationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a configuration file for a service",
    description=(
            "Create a new JSON configuration file for 'service_name' using the provided "
            "'config_name' and 'payload'. "
            "Responds with HTTP 404 for unknown services, HTTP 409 when the configuration "
            "already exists, and HTTP 422 for invalid names or payloads."
    ),
)
def add_service_config(
        service_name: str,
        body: AddConfigurationRequest,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> ConfigurationMutationResponse:
    """Create a new JSON config file for one service."""
    try:
        result = service.add_configuration(
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


@router.delete(
    CONFIG_SERVICE_ITEM,
    response_model=ConfigurationMutationResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove a configuration file from a service",
    description=(
            "Delete the JSON configuration file identified by 'config_name' from "
            "'service_name' and return whether the on-disk state changed. "
            "Responds with HTTP 404 when the service or configuration is unknown "
            "and HTTP 422 for invalid configuration names."
    ),
)
def remove_service_config(
        service_name: str,
        config_name: str,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> ConfigurationMutationResponse:
    """Remove one JSON config file for one service."""
    try:
        result = service.remove_configuration(service_name=service_name, config_name=config_name)
    except (UnknownConfigurationServiceError, ConfigurationNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidConfigurationNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigurationMutationResponse(**result.__dict__)


@router.patch(
    CONFIG_SERVICE_ITEM,
    response_model=ConfigurationMutationResponse,
    status_code=status.HTTP_200_OK,
    summary="Merge-patch a configuration file",
    description=(
            "Apply a JSON merge patch to the configuration file identified by "
            "'config_name' under 'service_name' and persist the result. "
            "Responds with HTTP 404 when the service or configuration is unknown "
            "and HTTP 422 for invalid names or payloads."
    ),
)
def patch_service_config(
        service_name: str,
        config_name: str,
        body: PatchConfigurationRequest,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> ConfigurationMutationResponse:
    """Merge-patch one JSON config file for one service."""
    try:
        result = service.patch_configuration(
            service_name=service_name,
            config_name=config_name,
            patch=body.patch,
        )
    except (UnknownConfigurationServiceError, ConfigurationNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (InvalidConfigurationNameError, ConfigurationValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ConfigurationMutationResponse(**result.__dict__)


@router.post(
    CONFIG_ENABLE_CONFIGURATIONS,
    response_model=BatchToggleConfigurationsResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable selected configuration files for a service",
    description=(
            "Set 'enabled: true' on each configuration listed in 'config_names' for "
            "'service_name' and return the per-config mutation results. "
            "Responds with HTTP 404 when the service is unknown."
    ),
)
def enable_configurations(
        service_name: str,
        body: BatchToggleConfigurationsRequest,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> BatchToggleConfigurationsResponse:
    """Enable selected configs for one service."""
    return _toggle_configurations(
        service_name=service_name,
        config_names=body.config_names,
        enabled=True,
        configuration_service=service,
    )


@router.post(
    CONFIG_DISABLE_CONFIGURATIONS,
    response_model=BatchToggleConfigurationsResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable selected configuration files for a service",
    description=(
            "Set 'enabled: false' on each configuration listed in 'config_names' for "
            "'service_name' and return the per-config mutation results. "
            "Responds with HTTP 404 when the service is unknown."
    ),
)
def disable_configurations(
        service_name: str,
        body: BatchToggleConfigurationsRequest,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> BatchToggleConfigurationsResponse:
    """Disable selected configs for one service."""
    return _toggle_configurations(
        service_name=service_name,
        config_names=body.config_names,
        enabled=False,
        configuration_service=service,
    )


@router.post(
    CONFIG_ENABLE_ALL_CONFIGURATIONS,
    response_model=BatchToggleConfigurationsResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable every configuration file for a service",
    description=(
            "Set 'enabled: true' on every configuration registered under 'service_name' "
            "and return the per-config mutation results. "
            "Responds with HTTP 404 when the service is unknown."
    ),
)
def enable_all_configurations(
        service_name: str,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> BatchToggleConfigurationsResponse:
    """Enable all configs for one service."""
    return _toggle_all_configurations(
        service_name=service_name,
        enabled=True,
        configuration_service=service,
    )


@router.post(
    CONFIG_DISABLE_ALL_CONFIGURATIONS,
    response_model=BatchToggleConfigurationsResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable every configuration file for a service",
    description=(
            "Set 'enabled: false' on every configuration registered under 'service_name' "
            "and return the per-config mutation results. "
            "Responds with HTTP 404 when the service is unknown."
    ),
)
def disable_all_configurations(
        service_name: str,
        service: ConfigurationServiceDep,
        _user: dict = Depends(require_internal),
) -> BatchToggleConfigurationsResponse:
    """Disable all configs for one service."""
    return _toggle_all_configurations(
        service_name=service_name,
        enabled=False,
        configuration_service=service,
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


################################################################################

def get_configuration_service(
        registry: ServiceRegistry = Depends(get_registry),
) -> ConfigurationService:
    """Resolve the ConfigurationService from the active service registry."""
    return get_typed_service(
        registry=registry,
        service_name=configuration.SERVICE_NAME,
        expected_type=ConfigurationService)


ConfigurationServiceDep = Annotated[ConfigurationService, Depends(get_configuration_service)]
