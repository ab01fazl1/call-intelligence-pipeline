"""The three pipeline stages. Each is a small StageHandler; all reliability logic is in base."""

import asyncio
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from callintel.analysis.model import CallView
from callintel.analysis.scoring import RULESETS, QAReport, analyze
from callintel.asr import make_backend
from callintel.asr.base import ASRRequest
from callintel.audio import preprocess as pp
from callintel.db.models import Call, CallStatus, QAResult, Transcript
from callintel.errors import PermanentError
from callintel.messaging.envelope import Stage
from callintel.metrics import ASR_RTF, QA_FLAGS
from callintel.storage import normalized_key, raw_key
from callintel.text.normalize import normalize
from callintel.workers.base import CallSnapshot, Deps, StageHandler


class PreprocessHandler(StageHandler[pp.PreprocessResult]):
    stage = Stage.PREPROCESS

    @property
    def version(self) -> str:
        return pp.VERSION

    async def run(self, call: CallSnapshot) -> pp.PreprocessResult:
        s = self.settings
        with tempfile.TemporaryDirectory(prefix="pp-") as tmp:
            tmpdir = Path(tmp)
            raw = tmpdir / "raw"
            await self.deps.store.download_to_file(raw_key(call.id), raw)
            # numpy releases the GIL for the heavy parts, so a thread keeps the event loop
            # (and AMQP heartbeats) responsive; ASR is heavier and gets a process instead.
            result = await asyncio.to_thread(
                pp.preprocess_file, raw, tmpdir, channel_map=s.channel_map,
                min_duration_s=s.min_call_duration_s, max_duration_s=s.max_call_duration_s,
            )
            for channel, path in enumerate(result.outputs):
                await self.deps.store.put_file(normalized_key(call.id, channel), path,
                                               content_type="audio/wav")
        return result

    async def persist(self, session: AsyncSession, call: Call,
                      result: pp.PreprocessResult) -> CallStatus:
        call.duration_s = result.duration_s
        call.sample_rate = result.source_sample_rate
        call.channels = result.channels
        call.audio_features = result.features
        call.attributes = {**call.attributes, "source_format": result.source_format}
        return CallStatus.TRANSCRIBING

    def summary(self, result: pp.PreprocessResult) -> dict[str, Any]:
        return {"duration_s": result.duration_s, "channels": result.channels,
                "source_sample_rate": result.source_sample_rate}


@dataclass(frozen=True)
class ASRResult:
    model: str
    segments: list[dict[str, Any]]
    processing_s: float
    real_time_factor: float | None


class ASRHandler(StageHandler[ASRResult]):
    stage = Stage.ASR
    invalidates_downstream = True  # a new transcript must be re-analysed, even if v2 ran

    def __init__(self, deps: Deps) -> None:
        super().__init__(deps)
        self.backend = make_backend(deps.settings)

    @property
    def version(self) -> str:
        return self.backend.name

    @property
    def concurrency(self) -> int:
        return self.settings.asr_concurrency

    async def start(self) -> None:
        await self.backend.start()

    async def close(self) -> None:
        await self.backend.close()

    def timeout_s(self, call: CallSnapshot) -> float:
        audio = (call.duration_s or 0) * (call.channels or 1)
        return max(self.settings.asr_timeout_floor_s, audio * self.settings.asr_timeout_rtf)

    async def run(self, call: CallSnapshot) -> ASRResult:
        if not call.channels or call.duration_s is None:
            raise PermanentError("call has not been preprocessed")
        speakers = pp.speakers_for(call.channels, self.settings.channel_map)
        segments: list[dict[str, Any]] = []
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="asr-") as tmp:
            for channel in range(call.channels):
                path = Path(tmp) / f"ch{channel}.wav"
                await self.deps.store.download_to_file(normalized_key(call.id, channel), path)
                request = ASRRequest(call.id, call.sha256, channel, call.channels, call.duration_s)
                for seg in await self.backend.transcribe(path, request):
                    segments.append({
                        "speaker": speakers[channel], "channel": channel,
                        "start": round(seg.start, 2), "end": round(seg.end, 2),
                        "text": seg.text, "confidence": seg.confidence,
                    })
        elapsed = time.perf_counter() - started
        rtf = elapsed / call.duration_s if call.duration_s else None
        if rtf is not None:
            ASR_RTF.labels(self.backend.name).observe(rtf)
        segments.sort(key=lambda s: (s["start"], s["channel"]))
        return ASRResult(self.backend.name, segments, round(elapsed, 3),
                         round(rtf, 4) if rtf is not None else None)

    async def persist(self, session: AsyncSession, call: Call, result: ASRResult) -> CallStatus:
        full_text = "\n".join(f"{s['speaker']}: {s['text']}" for s in result.segments)
        values = {
            "segments": result.segments,
            "full_text": full_text,
            "search_text": normalize(" ".join(s["text"] for s in result.segments)),
            "processing_s": result.processing_s,
            "real_time_factor": result.real_time_factor,
        }
        await session.execute(
            insert(Transcript)
            .values(call_id=call.id, model=result.model, language="fa", **values)
            .on_conflict_do_update(constraint="uq_transcripts_call_model", set_=values)
        )
        return CallStatus.ANALYZING

    def summary(self, result: ASRResult) -> dict[str, Any]:
        return {"model": result.model, "segments": len(result.segments),
                "real_time_factor": result.real_time_factor}


@dataclass(frozen=True)
class AnalysisResult:
    report: QAReport
    transcript_model: str


class AnalysisHandler(StageHandler[AnalysisResult]):
    stage = Stage.ANALYSIS

    def __init__(self, deps: Deps) -> None:
        super().__init__(deps)
        if deps.settings.analyzer_version not in RULESETS:
            raise ValueError(f"unknown ANALYZER_VERSION {deps.settings.analyzer_version!r}")

    @property
    def version(self) -> str:
        return self.settings.analyzer_version

    async def run(self, call: CallSnapshot) -> AnalysisResult:
        async with self.deps.sessions() as s:
            transcript = await s.scalar(
                select(Transcript).where(Transcript.call_id == call.id)
                .order_by(Transcript.created_at.desc()).limit(1)
            )
        if transcript is None:
            raise PermanentError("no transcript for call")
        view = CallView.from_segments(call.duration_s or 0.0, transcript.segments,
                                      call.audio_features)
        return AnalysisResult(analyze(view, self.version), transcript.model)

    async def persist(self, session: AsyncSession, call: Call,
                      result: AnalysisResult) -> CallStatus:
        report = result.report
        values = {
            "score": report.score,
            "compliance": report.compliance,
            "flags": report.flags,
            "details": report.to_dict(),
        }
        await session.execute(
            insert(QAResult)
            .values(call_id=call.id, analyzer_version=report.version,
                    transcript_model=result.transcript_model, **values)
            .on_conflict_do_update(constraint="uq_qa_results_call_version_model", set_=values)
        )
        for flag in report.flags:
            QA_FLAGS.labels(report.version, flag).inc()
        return CallStatus.COMPLETED

    def summary(self, result: AnalysisResult) -> dict[str, Any]:
        r = result.report
        return {"analyzer_version": r.version, "score": r.score, "compliance": r.compliance,
                "flags": r.flags}


HANDLERS: dict[str, type[StageHandler[Any]]] = {
    Stage.PREPROCESS.value: PreprocessHandler,
    Stage.ASR.value: ASRHandler,
    Stage.ANALYSIS.value: AnalysisHandler,
}
