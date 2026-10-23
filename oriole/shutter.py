"""Stimulation shutter on an NI-DAQ analog output, as Plerion drives it (0 V closed, 10 V open).

The output task is only held while the shutter is open, so the device stays free for Plerion
whenever the shutter is closed. nidaqmx is imported lazily: without it (or without the NI
drivers) Oriole still runs and the shutter calls raise ShutterError.
"""
from __future__ import annotations


class ShutterError(RuntimeError):
    pass


def _new_task(device: str, ao_channel: int):
    """Software-timed analog output task on <device>/ao<channel>, 0..10 V."""
    try:
        import nidaqmx
    except ImportError as e:
        raise ShutterError("nidaqmx is not installed (pip install nidaqmx; needs the NI-DAQmx driver)") from e
    task = nidaqmx.Task()
    try:
        task.ao_channels.add_ao_voltage_chan(f"{device}/ao{ao_channel}", min_val=0.0, max_val=10.0)
    except Exception:
        task.close()
        raise
    return task


class Shutter:
    def __init__(self, device: str = "Dev1", ao_channel: int = 0, open_v: float = 10.0):
        self.device = device
        self.ao_channel = ao_channel
        self.open_v = open_v
        self._task = None

    @property
    def channel(self) -> str:
        return f"{self.device}/ao{self.ao_channel}"

    @property
    def is_open(self) -> bool:
        return self._task is not None

    def _task_or_new(self):
        if self._task is not None:
            return self._task
        try:
            return _new_task(self.device, self.ao_channel)
        except ShutterError:
            raise
        except Exception as e:  # noqa: BLE001  (nidaqmx.DaqError: no device, reserved by Plerion...)
            raise ShutterError(f"cannot use {self.channel}: {e}") from e

    def open(self):
        task = self._task_or_new()
        try:
            task.write(float(self.open_v), auto_start=True)
        except Exception as e:  # noqa: BLE001
            task.close()
            self._task = None
            raise ShutterError(f"cannot open the shutter on {self.channel}: {e}") from e
        self._task = task

    def close(self):
        """Write 0 V and release the device, also when Oriole did not open the shutter itself."""
        task = self._task_or_new()
        self._task = None
        try:
            task.write(0.0, auto_start=True)
        except Exception as e:  # noqa: BLE001
            raise ShutterError(f"cannot close the shutter on {self.channel}: {e}") from e
        finally:
            task.close()
