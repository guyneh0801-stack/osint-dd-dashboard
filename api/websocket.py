"""WebSocket handler for real-time screening log streaming.

Each client connects to ``/ws/screening/{job_id}`` and receives:
1. All buffered log lines for that job (history replay).
2. New log lines as they are produced by the subprocess.
3. A ``complete`` message when the job finishes.

Connections are isolated per job; a client only sees logs for the job
specified in the URL path parameter.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from core.models import WSMessage
from core.persistence import State

# Maximum time to wait for a ping from the client before disconnecting.
_PING_TIMEOUT_SECONDS = 60


async def screening_ws(websocket: WebSocket, job_id: str, state: State) -> None:
    """Handle a WebSocket connection for *job_id*.

    Protocol:
    * On connect → accept, send buffered logs, register socket.
    * During session → echo ping messages, ignore everything else.
    * On disconnect → unregister socket and return cleanly.
    * All errors are caught to prevent a single bad connection from
      affecting other clients.
    """
    await websocket.accept()

    # Verify job exists
    job = await state.get_job(job_id)
    if job is None:
        err = WSMessage(
            type="error",
            job_id=job_id,
            payload={"message": f"Job {job_id} not found"},
        )
        await websocket.send_text(err.model_dump_json())
        await websocket.close(code=4004)
        return

    # Register connection
    await state.register_ws(job_id, websocket)

    try:
        # Send buffered history (oldest first)
        buffered = await state.get_buffered_logs(job_id)
        for line in buffered:
            await websocket.send_text(line)

        # Send current status
        status_msg = WSMessage(
            type="status",
            job_id=job_id,
            payload={"status": job.status},
        )
        await websocket.send_text(status_msg.model_dump_json())

        # Receive loop — handle pings and keep connection alive
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=_PING_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                # Send a ping to check liveness
                try:
                    ping = WSMessage(
                        type="ping", job_id=job_id, payload="server-ping"
                    )
                    await websocket.send_text(ping.model_dump_json())
                except Exception:
                    break
                continue

            # Handle client messages (only pings are meaningful)
            try:
                data = json.loads(raw)
                if data.get("type") == "ping":
                    pong = WSMessage(
                        type="ping", job_id=job_id, payload="pong"
                    )
                    await websocket.send_text(pong.model_dump_json())
            except json.JSONDecodeError:
                # Ignore malformed client messages
                pass

    except WebSocketDisconnect:
        pass
    except Exception:
        # Any other error → close connection cleanly
        pass
    finally:
        await state.unregister_ws(job_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
