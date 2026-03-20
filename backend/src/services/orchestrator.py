"""Async orchestration across inputs, providers, normalization, and evaluation."""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .config_loader import load_optimizer_configs, load_provider_configs, load_translator_configs
from .logging import get_logger, log_to_all_active_loggers, ensure_requests_log
from .models import EvaluationResult, GeneratedAudio, ProviderConfig, ProviderRunRecord, RunSummary, TTSRequest
from .optimization import AudioOptimizerBase, build_active_optimizers
from .output_paths import optimized_output_dir, scores_dir
from .providers import build_provider
from .evaluation import EvaluationEngine, JsonScoreStore, MultiScoreStore, SQLiteScoreStore
from .translation import build_translators, translate_with_fallback
from .generation.audio_normalizer import normalize_audio
from .generation.input_loader import load_requests
from .generation.result_writer import write_run_summary

logger = get_logger("services.orchestrator")


async def run_pipeline(
    *,
    base_dir: Path,
    input_path: Path,
    default_language: str,
    output_format: str,
    config_dir: Path | None = None,
    dotenv_path: Path | None = None,
    max_concurrency: int = 5,
    output_dir: Path | None = None,
) -> RunSummary:
    try:
        httpx_module = importlib.import_module("httpx")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency 'httpx'. Install project dependencies with "
            "`pip install -r requirements.txt`."
        ) from exc

    requests = load_requests(input_path, default_language)
    output_root = output_dir or base_dir
    ensure_requests_log(output_root)
    configs = load_provider_configs(base_dir=base_dir, config_dir=config_dir, dotenv_path=dotenv_path)
    providers = [(build_provider(config, output_root), config) for config in configs if config.generate.get("active")]

    if not requests:
        raise RuntimeError(f"No input texts were found in {input_path}.")
    if not providers:
        run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        message = (
            "No active generators were found. Enable generate.active for at "
            "least one provider in config/generators/*.json."
        )
        log_to_all_active_loggers(message, level=30)
        summary = RunSummary(
            run_id=run_id,
            input_file=str(input_path),
            output_format=output_format,
            provider_records=[],
        )
        write_run_summary(output_root, summary)
        return summary

    translator_configs = load_translator_configs(
        base_dir=base_dir, config_dir=config_dir, dotenv_path=dotenv_path,
    )
    translators = build_translators(translator_configs, base_dir)

    await _resolve_english_filenames(requests, translators)

    for language_code in {request.language_code for request in requests}:
        (output_root / f"tts_audio_{language_code}").mkdir(parents=True, exist_ok=True)
    logger.info(
        "pipeline_started input_path=%s request_count=%s provider_count=%s output_dir=%s",
        str(input_path),
        len(requests),
        len(providers),
        str(output_root),
    )

    semaphore = asyncio.Semaphore(max_concurrency)
    evaluation_engine = EvaluationEngine(base_dir=output_root)
    score_root = scores_dir(output_root)
    score_store = MultiScoreStore(
        [
            JsonScoreStore(score_root / "json"),
            SQLiteScoreStore(score_root / "sqlite" / "scores.db"),
        ]
    )

    async with httpx_module.AsyncClient(timeout=180.0) as client:
        tasks = [
            _process_provider_request(
                request=request,
                provider_config=config,
                provider=provider,
                output_format=output_format,
                semaphore=semaphore,
                client=client,
                evaluation_engine=evaluation_engine,
                score_store=score_store,
            )
            for request in requests
            for provider, config in providers
        ]
        records = list(await asyncio.gather(*tasks))

    optimizer_configs = load_optimizer_configs(base_dir=base_dir, config_dir=config_dir)
    optimizers = build_active_optimizers(optimizer_configs, output_root)
    if optimizers:
        await _run_optimizations(records, optimizers, output_root, semaphore)
    else:
        logger.info("optimization_skipped reason=no_active_optimizers")

    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    summary = RunSummary(
        run_id=run_id,
        input_file=str(input_path),
        output_format=output_format,
        provider_records=records,
    )
    write_run_summary(output_root, summary)
    logger.info("pipeline_finished run_id=%s record_count=%s", run_id, len(records))
    return summary


async def _resolve_english_filenames(requests, translators) -> None:
    """Translate non-English words to English so output files are named with English words."""
    if not translators:
        return
    for request in requests:
        if request.language_code == "en":
            continue
        english = await translate_with_fallback(
            translators,
            text=request.text,
            input_lang_code=request.language_code,
            target_lang_code="en",
        )
        request.filename_text = english


async def _process_provider_request(
    *,
    request: TTSRequest,
    provider_config: ProviderConfig,
    provider,
    output_format: str,
    semaphore: asyncio.Semaphore,
    client: Any,
    evaluation_engine: EvaluationEngine,
    score_store: MultiScoreStore,
) -> ProviderRunRecord:
    async with semaphore:
        request_log_context = f"request_id={request.request_id} provider={provider.name}"
        try:
            logger.info("provider_generation_started %s", request_log_context)
            generated_audio = await provider.generate(request, output_format, client)
        except Exception as exc:
            logger.exception("provider_generation_failed %s error=%s", request_log_context, str(exc))
            return ProviderRunRecord(
                request_id=request.request_id,
                provider=provider.name,
                source_file=request.source_file,
                source_type=request.source_type,
                language_code=request.language_code,
                language_name=request.language_name,
                expected_text=request.expected_text,
                filename_text=request.filename_text,
                output_format=output_format,
                raw_path="",
                normalized_path=None,
                generation_latency_seconds=0.0,
                evaluation_latency_seconds=None,
                evaluation_score=None,
                rolling_average_score=None,
                transcription=None,
                success=False,
                error=str(exc),
            )

        generated_audio = await normalize_audio(provider.base_dir, generated_audio)

        eval_active = provider_config.evaluate.get("active", False)
        if eval_active:
            logger.info("provider_evaluation_started %s", request_log_context)
            evaluation_result = await evaluation_engine.evaluate(generated_audio, provider_config, client)
            aggregate = await score_store.save(generated_audio, evaluation_result)
            evaluation_result.rolling_average = aggregate.average_score
        else:
            evaluation_result = EvaluationResult(
                provider=generated_audio.provider,
                request_id=generated_audio.request_id,
                expected_text=generated_audio.expected_text,
                transcription="",
                levenshtein_distance=0,
                char_accuracy=0.0,
                word_accuracy=0.0,
                pronunciation_accuracy=0.0,
                latency_seconds=0.0,
                success=False,
                error="Evaluation disabled in provider config.",
            )

        logger.info(
            "provider_request_completed %s success=%s error=%s",
            request_log_context,
            generated_audio.success,
            generated_audio.error,
        )
        return _build_record(request, generated_audio, evaluation_result)


def _build_record(
    request: TTSRequest,
    generated_audio: GeneratedAudio,
    evaluation_result: EvaluationResult,
) -> ProviderRunRecord:
    return ProviderRunRecord(
        request_id=request.request_id,
        provider=generated_audio.provider,
        source_file=request.source_file,
        source_type=request.source_type,
        language_code=request.language_code,
        language_name=request.language_name,
        expected_text=request.expected_text,
        filename_text=request.filename_text,
        output_format=generated_audio.output_format,
        raw_path=str(generated_audio.raw_path),
        normalized_path=str(generated_audio.normalized_path) if generated_audio.normalized_path else None,
        generation_latency_seconds=generated_audio.latency_seconds,
        evaluation_latency_seconds=evaluation_result.latency_seconds,
        evaluation_score=evaluation_result.pronunciation_accuracy if evaluation_result.success else None,
        rolling_average_score=evaluation_result.rolling_average,
        transcription=evaluation_result.transcription,
        success=generated_audio.success,
        error=generated_audio.error or generated_audio.normalization_error or evaluation_result.error,
    )


async def _run_optimizations(
    records: list[ProviderRunRecord],
    optimizers: list[AudioOptimizerBase],
    output_root: Path,
    semaphore: asyncio.Semaphore,
) -> None:
    """Run every active optimizer on each successfully generated audio file."""
    logger.info(
        "optimization_started optimizer_count=%s record_count=%s",
        len(optimizers),
        len(records),
    )
    for record in records:
        if not record.success or not record.raw_path:
            continue
        raw = Path(record.raw_path)
        if not raw.exists():
            continue

        for optimizer in optimizers:
            out_dir = optimized_output_dir(
                output_root, optimizer.name, record.language_code, record.provider,
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / raw.name

            try:
                async with semaphore:
                    await optimizer.optimize(raw, out_path)
                record.optimized_paths[optimizer.name] = str(out_path)
                logger.info(
                    "optimization_complete optimizer=%s path=%s",
                    optimizer.name,
                    str(out_path),
                )
            except Exception as exc:
                logger.error(
                    "optimization_failed optimizer=%s raw_path=%s error=%s",
                    optimizer.name,
                    str(raw),
                    str(exc),
                )
