"""Single-use decoder subprocess. Only file paths and a MIME enum cross this boundary."""

import json
import sys
from pathlib import Path


def constrain():
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        return None
    import ctypes
    from ctypes import wintypes as w

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", w.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", w.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", w.DWORD),
            ("SchedulingClass", w.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", IO),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    k.CreateJobObjectW.restype = w.HANDLE
    k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    k.SetInformationJobObject.restype = w.BOOL
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k.AssignProcessToJobObject.restype = w.BOOL
    k.GetCurrentProcess.restype = w.HANDLE
    job = k.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x100 | 0x2
    info.BasicLimitInformation.PerProcessUserTimeLimit = 100_000_000
    info.ProcessMemoryLimit = 768 * 1024 * 1024
    if (
        not job
        or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        or not k.AssignProcessToJobObject(job, k.GetCurrentProcess())
    ):
        raise RuntimeError("decoder_resource_limit_unavailable")
    return job


def main():
    _job = constrain()  # Keep the resource-limit handle in process scope.
    from io import BytesIO

    from PIL import Image

    from visual_platform.domain.image_limits import MAX_CANONICAL_PIXELS
    from visual_platform.imaging.normalize import normalize

    original = Path(sys.argv[1]).read_bytes()
    Image.MAX_IMAGE_PIXELS = MAX_CANONICAL_PIXELS
    with Image.open(BytesIO(original)) as image:
        mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(
            image.format or ""
        )
        if mime != sys.argv[3]:
            raise ValueError("mime_mismatch")
        original_size = image.size
    result = normalize(original)
    Path(sys.argv[2]).write_bytes(result)
    with Image.open(BytesIO(result)) as image:
        size = image.size
    print(
        json.dumps(
            {
                "mime": mime,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "width": size[0],
                "height": size[1],
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        print('{"error":"invalid_image"}')
        raise SystemExit(2)
    except Exception as exc:
        if type(exc).__name__ in (
            "DecompressionBombError",
            "DecompressionBombWarning",
            "PyCMSError",
        ):
            print('{"error":"invalid_image"}')
            raise SystemExit(2)
        print('{"error":"decoder_failed"}')
        raise SystemExit(3)
