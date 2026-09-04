"""
FastAPI router for recording the MM01 stream to CSV.

Mounted at /recording.  Starting a recording needs a running device manager;
listing, downloading and deleting saved recordings do not — a student can pull
yesterday's data off the Pi with the MM01 unplugged.

    POST   /recording/start                 begin, returns the session
    POST   /recording/stop                  end, returns the finished session
    GET    /recording/status                is it recording, and how far in
    GET    /recording/sessions              every saved recording, newest first
    GET    /recording/sessions/{id}         one recording's metadata
    GET    /recording/sessions/{id}/download  the CSV file
    DELETE /recording/sessions/{id}         delete the CSV and its metadata

File and thread work is dispatched to the thread pool with run_in_executor so
the event loop is never blocked, matching app/routers/mm01.py.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.recorder import Recorder, RecordingError
from app.models.recording_models import (
    RecordingCommandResponse,
    RecordingListResponse,
    RecordingSessionModel,
    RecordingStatusResponse,
    StartRecordingRequest,
)

router = APIRouter(prefix="/recording", tags=["Recording"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_recorder(request: Request) -> Recorder:
    recorder: Recorder | None = getattr(request.app.state, "recorder", None)
    if recorder is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recording is not available — the recordings directory could not be opened.",
        )
    return recorder


def _get_manager(request: Request):
    manager = getattr(request.app.state, "mm01_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No MM01 device manager is running, so there is nothing to record.",
        )
    return manager


async def _run(fn, *args, **kwargs):
    """Run a blocking function in the default thread executor."""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
    return await loop.run_in_executor(None, fn, *args)


def _status_response(recorder: Recorder) -> RecordingStatusResponse:
    state = recorder.status()
    session = state["session"]
    return RecordingStatusResponse(
        recording=state["recording"],
        session=RecordingSessionModel(**session) if session else None,
        directory=str(recorder.directory),
        default_interval_ms=recorder.default_interval_ms,
        max_seconds=recorder.max_seconds,
    )


def _require_session(recorder: Recorder, session_id: str) -> dict:
    session = recorder.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No recording {session_id}")
    return session


# ── Control ───────────────────────────────────────────────────────────────────

@router.get("/status", response_model=RecordingStatusResponse,
            summary="Is a recording running, and how far into it")
async def recording_status(request: Request):
    """Safe to poll — this reads counters, not the device."""
    return _status_response(_get_recorder(request))


@router.post("/start", response_model=RecordingSessionModel,
             summary="Start recording to a CSV file")
async def start_recording(body: StartRecordingRequest, request: Request):
    """Begin writing one row per sample interval to `recordings/<id>.csv`.

    The interval selects how often the *latest* conversion is written down; the
    MM01 keeps converting at 80 samples/second regardless. 409 if a recording is
    already running.
    """
    recorder = _get_recorder(request)
    manager = _get_manager(request)
    try:
        session = await _run(
            recorder.start, manager, body.name, body.note,
            body.sample_interval_ms, body.device_indexes,
        )
    except RecordingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return RecordingSessionModel(**session)


@router.post("/stop", response_model=RecordingSessionModel,
             summary="Stop the current recording")
async def stop_recording(request: Request):
    """Close the file and return the finished session. 409 if not recording."""
    recorder = _get_recorder(request)
    try:
        session = await _run(recorder.stop)
    except RecordingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return RecordingSessionModel(**session)


# ── Saved recordings ──────────────────────────────────────────────────────────

@router.get("/sessions", response_model=RecordingListResponse,
            summary="List saved recordings, newest first")
async def list_sessions(request: Request):
    recorder = _get_recorder(request)
    sessions = await _run(recorder.list_sessions)
    return RecordingListResponse(
        sessions=[RecordingSessionModel(**s) for s in sessions],
        total_sessions=len(sessions),
    )


@router.get("/sessions/{session_id}", response_model=RecordingSessionModel,
            summary="Get one recording's metadata")
async def get_session(session_id: str, request: Request):
    """Gage factor, bridge, zero offset and row count for a saved recording."""
    recorder = _get_recorder(request)
    return RecordingSessionModel(**_require_session(recorder, session_id))


@router.get("/sessions/{session_id}/download", response_class=FileResponse,
            summary="Download a recording as CSV")
async def download_session(session_id: str, request: Request):
    """The CSV itself. A recording still in progress downloads what is flushed."""
    recorder = _get_recorder(request)
    _require_session(recorder, session_id)
    path = recorder.csv_path(session_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No CSV file for {session_id}")
    return FileResponse(path, media_type="text/csv", filename=path.name)


@router.delete("/sessions/{session_id}", response_model=RecordingCommandResponse,
               summary="Delete a recording")
async def delete_session(session_id: str, request: Request):
    recorder = _get_recorder(request)
    _require_session(recorder, session_id)
    try:
        await _run(recorder.delete_session, session_id)
    except RecordingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return RecordingCommandResponse(message=f"Recording {session_id} deleted")
