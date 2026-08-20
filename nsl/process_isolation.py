from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import multiprocessing
from typing import Callable


MAX_ISOLATED_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_ISOLATED_TIMEOUT_MS = 2_147_483_647
_FRAME_COMPLETED = b"\x00"
_FRAME_TARGET_ERROR = b"\x01"
_FRAME_PROTOCOL_ERROR = b"\x02"


class ProcessIsolationError(RuntimeError):
    pass


class IsolatedProcessStatus(StrEnum):
    COMPLETED = "COMPLETED"
    TARGET_ERROR = "TARGET_ERROR"
    CRASHED = "CRASHED"
    TIMED_OUT = "TIMED_OUT"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"


@dataclass(frozen=True, slots=True)
class IsolatedProcessResult:
    status: IsolatedProcessStatus
    payload: bytes | None
    exit_code: int | None
    error_code: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, IsolatedProcessStatus):
            raise ValueError("isolated process status is invalid")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("isolated process exit_code must be an integer or null")
        if self.status is IsolatedProcessStatus.COMPLETED:
            if type(self.payload) is not bytes:
                raise ValueError("completed isolated process requires bytes payload")
            if self.exit_code != 0 or self.error_code is not None:
                raise ValueError("completed isolated process metadata is inconsistent")
            return
        if self.payload is not None:
            raise ValueError("failed isolated process must not expose a payload")
        if not isinstance(self.error_code, str) or not self.error_code:
            raise ValueError("failed isolated process requires an error code")
        if self.status is IsolatedProcessStatus.CRASHED and (
            self.exit_code is None or self.exit_code == 0
        ):
            raise ValueError("crashed isolated process requires a non-zero exit code")


class ProcessIsolatedRuntime:
    """Runs a trusted bytes-to-bytes Runtime entrypoint in a spawned process."""

    def __init__(
        self,
        target: Callable[[bytes], bytes],
        *,
        timeout_ms: int = 30_000,
        max_payload_bytes: int = MAX_ISOLATED_PAYLOAD_BYTES,
    ) -> None:
        if not callable(target):
            raise TypeError("isolated Runtime target must be callable")
        if (
            type(timeout_ms) is not int
            or timeout_ms < 1
            or timeout_ms > MAX_ISOLATED_TIMEOUT_MS
        ):
            raise ValueError(
                f"isolated timeout_ms must be between 1 and {MAX_ISOLATED_TIMEOUT_MS}"
            )
        if (
            type(max_payload_bytes) is not int
            or max_payload_bytes < 1
            or max_payload_bytes > MAX_ISOLATED_PAYLOAD_BYTES
        ):
            raise ValueError(
                "isolated max_payload_bytes must be between 1 and "
                f"{MAX_ISOLATED_PAYLOAD_BYTES}"
            )
        self.target = target
        self.timeout_ms = timeout_ms
        self.max_payload_bytes = max_payload_bytes

    def execute(self, payload: bytes) -> IsolatedProcessResult:
        if type(payload) is not bytes:
            raise TypeError("isolated Runtime payload must be bytes")
        if len(payload) > self.max_payload_bytes:
            raise ValueError("isolated Runtime payload exceeds the configured limit")

        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_isolated_entry,
            args=(self.target, payload, sender, self.max_payload_bytes),
        )
        try:
            process.start()
        except Exception as error:
            receiver.close()
            sender.close()
            raise ProcessIsolationError("isolated Runtime process could not start") from error
        sender.close()

        try:
            if receiver.poll(self.timeout_ms / 1000):
                try:
                    frame = receiver.recv_bytes(self.max_payload_bytes + 1)
                except (EOFError, OSError):
                    process.join()
                    return self._without_frame(process.exitcode)
                process.join()
                return self._from_frame(frame, process.exitcode)

            if process.is_alive():
                process.terminate()
                process.join()
                return IsolatedProcessResult(
                    IsolatedProcessStatus.TIMED_OUT,
                    None,
                    process.exitcode,
                    "ISOLATED_RUNTIME_TIMEOUT",
                )
            process.join()
            return self._without_frame(process.exitcode)
        finally:
            receiver.close()

    def _without_frame(self, exit_code: int | None) -> IsolatedProcessResult:
        if exit_code not in {None, 0}:
            return IsolatedProcessResult(
                IsolatedProcessStatus.CRASHED,
                None,
                exit_code,
                "ISOLATED_RUNTIME_CRASH",
            )
        return IsolatedProcessResult(
            IsolatedProcessStatus.PROTOCOL_ERROR,
            None,
            exit_code,
            "ISOLATED_RUNTIME_PROTOCOL_ERROR",
        )

    def _from_frame(
        self, frame: bytes, exit_code: int | None
    ) -> IsolatedProcessResult:
        if exit_code not in {None, 0}:
            return self._without_frame(exit_code)
        if frame.startswith(_FRAME_COMPLETED):
            return IsolatedProcessResult(
                IsolatedProcessStatus.COMPLETED,
                frame[1:],
                0,
                None,
            )
        if frame == _FRAME_TARGET_ERROR:
            return IsolatedProcessResult(
                IsolatedProcessStatus.TARGET_ERROR,
                None,
                0,
                "ISOLATED_RUNTIME_TARGET_ERROR",
            )
        return IsolatedProcessResult(
            IsolatedProcessStatus.PROTOCOL_ERROR,
            None,
            exit_code,
            "ISOLATED_RUNTIME_PROTOCOL_ERROR",
        )


def _isolated_entry(
    target: Callable[[bytes], bytes],
    payload: bytes,
    sender: object,
    max_payload_bytes: int,
) -> None:
    try:
        try:
            result = target(payload)
        except Exception:
            sender.send_bytes(_FRAME_TARGET_ERROR)
            return
        if type(result) is not bytes or len(result) > max_payload_bytes:
            sender.send_bytes(_FRAME_PROTOCOL_ERROR)
            return
        sender.send_bytes(_FRAME_COMPLETED + result)
    finally:
        sender.close()
