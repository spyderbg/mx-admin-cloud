"""Async TTS pipeline orchestration.

Accepts a pre-built ServiceRegistry (from lifespan) instead of loading configs
from disk on every run.  All service interactions go through internal HTTP
endpoints — ``core/`` never imports ``services/`` directly.
"""

# pylint: disable=duplicate-code

import logging

logger = logging.getLogger("core.pipeline")
#  #
# async def run_pipeline(
#     *,
#     registry: Any,
#     internal_token: str,
#     input_path: Path,
#     default_language: str,
#     output_format: str,
#     max_concurrency: int = 5,
#     output_dir: Path,
# ) -> RunSummary:
#     """Run the full TTS pipeline using pre-built service instances from the registry."""
#     try:
#         httpx_module = importlib.import_module("httpx")
#     except ModuleNotFoundError as exc:
#         raise RuntimeError(
#             "Missing dependency 'httpx'. Install project dependencies with "
#             "`pip install -r requirements.txt`."
#         ) from exc
#
#     # File rows for TTS must be built as list[GenerateRequest] (see api/internal/schemas); load_words is
#     # translation-only (core.input_loader.load_words).
#     requests: list[Any] = []
#     ensure_requests_log(output_dir)
#
#     provider_names_and_configs = registry.provider_names_and_configs()
#     optimizer_names = registry.optimizer_names()
#
#     if not requests:
#         raise RuntimeError(f"No input texts were found in {input_path}.")
#     if not provider_names_and_configs:
#         run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
#         message = (
#             "No active generators were found. Set generate.enabled to true for at "
#             "least one provider in config/text_to_speech/*.json."
#         )
#         log_to_all_active_loggers(message, level=30)
#         summary = RunSummary(
#             run_id=run_id,
#             input_file=str(input_path),
#             output_format=output_format,
#             provider_records=[],
#         )
#         write_run_summary(output_dir, summary)
#         return summary
#
#     for language_code in {request.language_code for request in requests}:
#         (output_dir / f"tts_audio_{language_code}").mkdir(parents=True, exist_ok=True)
#     logger.info(
#         "pipeline_started input_path=%s request_count=%s provider_count=%s output_dir=%s",
#         str(input_path),
#         len(requests),
#         len(provider_names_and_configs),
#         str(output_dir),
#     )
#
#     semaphore = asyncio.Semaphore(max_concurrency)
#     base_url = settings.API_BASE_URL
#     headers = {"Authorization": f"Bearer {internal_token}"}
#
#     async with httpx_module.AsyncClient(timeout=180.0) as client:
#         await _resolve_english_filenames(requests, client, internal_token)
#         tasks = [
#             _process_provider_request(
#                 request=request,
#                 provider_name=pname,
#                 provider_config=config,
#                 output_format=output_format,
#                 semaphore=semaphore,
#                 client=client,
#                 base_url=base_url,
#                 headers=headers,
#                 output_dir=output_dir,
#             )
#             for request in requests
#             for pname, config in provider_names_and_configs
#         ]
#         records = list(await asyncio.gather(*tasks))
#
#     if optimizer_names:
#         await _run_optimizations(records, optimizer_names, output_dir, semaphore, base_url, headers)
#     else:
#         logger.info("optimization_skipped reason=no_active_optimizers")
#
#     run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
#     summary = RunSummary(
#         run_id=run_id,
#         input_file=str(input_path),
#         output_format=output_format,
#         provider_records=records,
#     )
#     write_run_summary(output_dir, summary)
#     logger.info("pipeline_finished run_id=%s record_count=%s", run_id, len(records))
#     return summary
#
#
# async def _resolve_english_filenames(requests, client: Any, internal_token: str) -> None:
#     """Translate non-English words to English so output files are named with English words."""
#     translate_url = f"{settings.API_BASE_URL}/internal/translate"
#     headers = {"Authorization": f"Bearer {internal_token}"}
#     for request in requests:
#         if request.language_code == "en":
#             continue
#         try:
#             response = await client.post(
#                 translate_url,
#                 json={
#                     "text": request.text,
#                     "input_lang_code": request.language_code,
#                     "target_lang_code": "en",
#                 },
#                 headers=headers,
#             )
#             response.raise_for_status()
#             request.filename_text = response.json()["translated_text"]
#         except Exception as exc:
#             logger.warning(
#                 "filename_translation_failed request_id=%s error=%s",
#                 request.request_id,
#                 str(exc),
#             )
#
#
# async def _process_provider_request(
#     *,
#     request: GenerateRequest,
#     provider_name: str,
#     provider_config: Any,
#     output_format: str,
#     semaphore: asyncio.Semaphore,
#     client: Any,
#     base_url: str,
#     headers: dict[str, str],
#     output_dir: Path,
# ) -> ProviderRunRecord:
#     async with semaphore:
#         request_log_context = f"request_id={request.request_id} provider={provider_name}"
#         try:
#             logger.info("provider_generation_started %s", request_log_context)
#             gen_response = await client.post(
#                 f"{base_url}/internal/generators/{provider_name}/generate",
#                 json={
#                     "request_id": request.request_id,
#                     "text": request.text,
#                     "language_code": request.language_code,
#                     "language_name": request.language_name,
#                     "filename_text": request.filename_text,
#                     "expected_text": request.expected_text,
#                     "source_file": request.source_file,
#                     "source_type": request.source_type,
#                     "output_format": output_format,
#                     "row_index": request.row_index,
#                     "column_index": request.column_index,
#                 },
#                 headers=headers,
#             )
#             gen_response.raise_for_status()
#             gen_data = gen_response.json()
#         except Exception as exc:
#             logger.exception("provider_generation_failed %s error=%s", request_log_context, str(exc))
#             return ProviderRunRecord(
#                 request_id=request.request_id,
#                 provider=provider_name,
#                 source_file=request.source_file,
#                 source_type=request.source_type,
#                 language_code=request.language_code,
#                 language_name=request.language_name,
#                 expected_text=request.expected_text,
#                 filename_text=request.filename_text,
#                 output_format=output_format,
#                 raw_path="",
#                 normalized_path=None,
#                 generation_latency_seconds=0.0,
#                 evaluation_latency_seconds=None,
#                 evaluation_score=None,
#                 rolling_average_score=None,
#                 transcription=None,
#                 success=False,
#                 error=str(exc),
#             )
#
#         generated_audio = GeneratedAudio(
#             provider=gen_data["provider"],
#             request_id=gen_data["request_id"],
#             language_code=gen_data["language_code"],
#             language_name=gen_data["language_name"],
#             expected_text=gen_data["expected_text"],
#             filename_text=gen_data["filename_text"],
#             output_format=gen_data["output_format"],
#             raw_path=Path(gen_data["raw_path"]),
#             raw_extension=gen_data["raw_extension"],
#             latency_seconds=gen_data["latency_seconds"],
#             success=gen_data["success"],
#             error=gen_data.get("error"),
#             normalized_path=Path(gen_data["normalized_path"]) if gen_data.get("normalized_path") else None,
#             normalization_error=gen_data.get("normalization_error"),
#         )
#
#         eval_active = provider_config.evaluate.get("enabled", False)
#         if eval_active:
#             logger.info("provider_evaluation_started %s", request_log_context)
#             evaluation_result = await _evaluate_via_http(
#                 client, base_url, headers, generated_audio, provider_config, output_dir,
#             )
#         else:
#             evaluation_result = EvaluationResult(
#                 provider=generated_audio.provider,
#                 request_id=generated_audio.request_id,
#                 expected_text=generated_audio.expected_text,
#                 transcription="",
#                 levenshtein_distance=0,
#                 char_accuracy=0.0,
#                 word_accuracy=0.0,
#                 pronunciation_accuracy=0.0,
#                 latency_seconds=0.0,
#                 success=False,
#                 error="Evaluation disabled in provider config.",
#             )
#
#         logger.info(
#             "provider_request_completed %s success=%s error=%s",
#             request_log_context,
#             generated_audio.success,
#             generated_audio.error,
#         )
#         return _build_record(request, generated_audio, evaluation_result)
#
#
# async def _evaluate_via_http(
#     client: Any,
#     base_url: str,
#     headers: dict[str, str],
#     generated_audio: GeneratedAudio,
#     provider_config: Any,
#     output_dir: Path,
# ) -> EvaluationResult:
#     """Call the /internal/evaluators/evaluate endpoint."""
#     try:
#         eval_response = await client.post(
#             f"{base_url}/internal/evaluators/evaluate",
#             json={
#                 "provider": generated_audio.provider,
#                 "request_id": generated_audio.request_id,
#                 "expected_text": generated_audio.expected_text,
#                 "language_code": generated_audio.language_code,
#                 "language_name": generated_audio.language_name,
#                 "filename_text": generated_audio.filename_text,
#                 "output_format": generated_audio.output_format,
#                 "raw_path": str(generated_audio.raw_path),
#                 "raw_extension": generated_audio.raw_extension,
#                 "latency_seconds": generated_audio.latency_seconds,
#                 "normalized_path": str(generated_audio.normalized_path) if generated_audio.normalized_path else None,
#                 "normalization_error": generated_audio.normalization_error,
#                 "evaluate_config": provider_config.evaluate,
#                 "output_dir": str(output_dir),
#             },
#             headers=headers,
#         )
#         eval_response.raise_for_status()
#         eval_data = eval_response.json()
#         return EvaluationResult(
#             provider=eval_data["provider"],
#             request_id=eval_data["request_id"],
#             expected_text=eval_data["expected_text"],
#             transcription=eval_data["transcription"],
#             levenshtein_distance=eval_data["levenshtein_distance"],
#             char_accuracy=eval_data["char_accuracy"],
#             word_accuracy=eval_data["word_accuracy"],
#             pronunciation_accuracy=eval_data["pronunciation_accuracy"],
#             latency_seconds=eval_data["latency_seconds"],
#             success=eval_data["success"],
#             error=eval_data.get("error"),
#             rolling_average=eval_data.get("rolling_average"),
#         )
#     except Exception as exc:
#         logger.error("evaluation_http_failed error=%s", str(exc))
#         return EvaluationResult(
#             provider=generated_audio.provider,
#             request_id=generated_audio.request_id,
#             expected_text=generated_audio.expected_text,
#             transcription="",
#             levenshtein_distance=0,
#             char_accuracy=0.0,
#             word_accuracy=0.0,
#             pronunciation_accuracy=0.0,
#             latency_seconds=0.0,
#             success=False,
#             error=f"Evaluation HTTP call failed: {exc}",
#         )
#
#
# def _build_record(
#     request: GenerateRequest,
#     generated_audio: GeneratedAudio,
#     evaluation_result: EvaluationResult,
# ) -> ProviderRunRecord:
#     return ProviderRunRecord(
#         request_id=request.request_id,
#         provider=generated_audio.provider,
#         source_file=request.source_file,
#         source_type=request.source_type,
#         language_code=request.language_code,
#         language_name=request.language_name,
#         expected_text=request.expected_text,
#         filename_text=request.filename_text,
#         output_format=generated_audio.output_format,
#         raw_path=str(generated_audio.raw_path),
#         normalized_path=str(generated_audio.normalized_path) if generated_audio.normalized_path else None,
#         generation_latency_seconds=generated_audio.latency_seconds,
#         evaluation_latency_seconds=evaluation_result.latency_seconds,
#         evaluation_score=evaluation_result.pronunciation_accuracy if evaluation_result.success else None,
#         rolling_average_score=evaluation_result.rolling_average,
#         transcription=evaluation_result.transcription,
#         success=generated_audio.success,
#         error=generated_audio.error or generated_audio.normalization_error or evaluation_result.error,
#     )
#
#
# async def _run_optimizations(
#     records: list[ProviderRunRecord],
#     optimizer_names: list[str],
#     _output_root: Path,
#     semaphore: asyncio.Semaphore,
#     base_url: str,
#     headers: dict[str, str],
# ) -> None:
#     """Run every active optimizer on each successfully generated audio file via HTTP."""
#     try:
#         httpx_module = importlib.import_module("httpx")
#     except ModuleNotFoundError:
#         logger.error("optimization_skipped reason=httpx_not_installed")
#         return
#
#     logger.info(
#         "optimization_started optimizer_count=%s record_count=%s",
#         len(optimizer_names),
#         len(records),
#     )
#
#     async with httpx_module.AsyncClient(timeout=180.0) as client:
#         for record in records:
#             if not record.success or not record.raw_path:
#                 continue
#             raw = Path(record.raw_path)
#             if not raw.exists():
#                 continue
#
#             for opt_name in optimizer_names:
#                 out_dir = settings.optimized_output_dir(opt_name, record.language_code, record.provider)
#                 out_dir.mkdir(parents=True, exist_ok=True)
#                 out_path = out_dir / raw.name
#
#                 try:
#                     async with semaphore:
#                         opt_response = await client.post(
#                             f"{base_url}/internal/optimizers/{opt_name}/optimize",
#                             json={
#                                 "raw_path": str(raw),
#                                 "output_path": str(out_path),
#                             },
#                             headers=headers,
#                         )
#                         opt_response.raise_for_status()
#                     record.optimized_paths[opt_name] = str(out_path)
#                     logger.info(
#                         "optimization_complete optimizer=%s path=%s",
#                         opt_name,
#                         str(out_path),
#                     )
#                 except Exception as exc:
#                     logger.error(
#                         "optimization_failed optimizer=%s raw_path=%s error=%s",
#                         opt_name,
#                         str(raw),
#                         str(exc),
#                     )
