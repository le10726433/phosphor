"""Output device list, choice and the system default (S2, 0.91.1-dev; no Qt).

PortAudio reads the device list and the default output once, when it initialises, and
never again: after the system's output changed, playback kept the device that was the
default at startup. refresh() re-initialises it (about 3 ms on Apple M5) before a stream
opens, while no stream is open. The system default is watched with the platform's own
API, which works while a stream is open: Core Audio's default output device on macOS and
the MMDevice API's default render endpoint on Windows, both through ctypes.

Devices are chosen by name, because PortAudio's indexes change when it re-initialises.
The list holds one host API's devices: Core Audio on macOS, WASAPI on Windows (one entry
per real device, as in the Windows Sound settings; MME, DirectSound and WDM-KS repeat them).
"""
import sys
import threading

SYSTEM_DEFAULT = ''  # The preference value for "System default".

# Serialises PortAudio re-initialisation and device queries between the GUI and audio threads.
_lock = threading.RLock()


def refresh(sd):
    """Re-read PortAudio's devices and default. Only call it with no stream open (it closes
    every stream). A stand-in module without PortAudio (simulated_output) is left as it is."""
    terminate, initialize = getattr(sd, '_terminate', None), getattr(sd, '_initialize', None)
    if terminate is None or initialize is None: return
    with _lock:
        terminate(); initialize()


def _host_api(sd):
    """Index of the host API whose devices are listed, or None for every device."""
    wanted = 'Core Audio' if sys.platform == 'darwin' else 'WASAPI' if sys.platform == 'win32' else None
    if wanted is None: return None
    for index, api in enumerate(sd.query_hostapis()):
        if wanted in api['name']: return index
    return None


def outputs(sd):
    """[(PortAudio index, name)] of the output devices, each name once, in PortAudio's order."""
    with _lock:
        try:
            devices = sd.query_devices()
            api = _host_api(sd)
        except Exception:
            return []
        if isinstance(devices, dict): devices = [devices]  # One device (a stand-in module).
        seen, result = set(), []
        for position, info in enumerate(devices):
            index = info.get('index', position)
            if info.get('max_output_channels', 0) <= 0 or (api is not None and info.get('hostapi') != api): continue
            if info['name'] in seen: continue
            seen.add(info['name']); result.append((index, info['name']))
        return result


def names(sd):
    return [name for _, name in outputs(sd)]


def find(sd, name):
    """The PortAudio index of the named output device, or None when it is not connected."""
    return next((index for index, device in outputs(sd) if device == name), None)


def device_name(sd, device=None):
    """The name of a PortAudio output device; None asks for the default output."""
    with _lock:
        try:
            info = sd.query_devices(device, 'output') if device is not None else sd.query_devices(kind='output')
            return str(info['name'])
        except Exception:
            return ''


def default_token():
    """An identifier of the system's current default output, or None where it can't be read.
    Compared between calls to notice that the default changed; cheap (under 1 ms on macOS)."""
    try:
        if sys.platform == 'darwin': return _CoreAudio.get().default_output()
        if sys.platform == 'win32': return _windows_default_output()
    except Exception:
        return None
    return None


def present(name):
    """Whether an output device of this name is connected: True, False, or None where the
    platform can't say while a stream is open (Windows: a lost device shows as a stream error)."""
    try:
        if sys.platform == 'darwin': return name in _CoreAudio.get().output_names()
    except Exception:
        return None
    return None


class _CoreAudio:
    """The few Core Audio calls this needs, through ctypes."""
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance

    def __init__(self):
        import ctypes, ctypes.util
        self.ctypes = ctypes
        self.audio = ctypes.CDLL(ctypes.util.find_library('CoreAudio'))
        self.foundation = ctypes.CDLL(ctypes.util.find_library('CoreFoundation'))

        class Address(ctypes.Structure):
            _fields_ = [('selector', ctypes.c_uint32), ('scope', ctypes.c_uint32), ('element', ctypes.c_uint32)]
        self.Address = Address
        size_p = ctypes.POINTER(ctypes.c_uint32)
        self.audio.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32,
                                                          ctypes.c_void_p, size_p, ctypes.c_void_p]
        self.audio.AudioObjectGetPropertyData.restype = ctypes.c_int32
        self.audio.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32,
                                                              ctypes.c_void_p, size_p]
        self.audio.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        self.foundation.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        self.foundation.CFStringGetCString.restype = ctypes.c_bool
        self.foundation.CFRelease.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _code(text):
        return int.from_bytes(text.encode('ascii'), 'big')

    def _address(self, selector, scope='glob'):
        return self.Address(self._code(selector), self._code(scope), 0)

    def default_output(self):
        device = self.ctypes.c_uint32(); size = self.ctypes.c_uint32(4)
        status = self.audio.AudioObjectGetPropertyData(1, self.ctypes.byref(self._address('dOut')), 0, None,
                                                       self.ctypes.byref(size), self.ctypes.byref(device))
        return None if status or not device.value else device.value

    def _name(self, device):
        """kAudioObjectPropertyName, the name PortAudio reports for Core Audio devices."""
        ctypes = self.ctypes
        ref = ctypes.c_void_p(); size = ctypes.c_uint32(ctypes.sizeof(ref))
        if self.audio.AudioObjectGetPropertyData(device, ctypes.byref(self._address('lnam')), 0, None,
                                                 ctypes.byref(size), ctypes.byref(ref)) or not ref.value:
            return ''
        buffer = ctypes.create_string_buffer(1024)
        ok = self.foundation.CFStringGetCString(ref, buffer, len(buffer), 0x08000100)  # UTF-8
        self.foundation.CFRelease(ref)
        return buffer.value.decode('utf-8') if ok else ''

    def output_names(self):
        ctypes = self.ctypes
        size = ctypes.c_uint32()
        address = self._address('dev#')
        if self.audio.AudioObjectGetPropertyDataSize(1, ctypes.byref(address), 0, None, ctypes.byref(size)): return set()
        devices = (ctypes.c_uint32 * (size.value // 4))()
        if self.audio.AudioObjectGetPropertyData(1, ctypes.byref(address), 0, None, ctypes.byref(size), devices): return set()
        result = set()
        for device in devices:
            streams = ctypes.c_uint32()
            if self.audio.AudioObjectGetPropertyDataSize(device, ctypes.byref(self._address('stm#', 'outp')), 0, None,
                                                         ctypes.byref(streams)) or not streams.value:
                continue  # No output streams: an input-only device.
            result.add(self._name(device))
        return result


def _windows_default_output():
    """The endpoint ID of Windows' default render device (eRender, eConsole), through the
    MMDevice API's COM interfaces called by vtable index with ctypes."""
    import ctypes
    import uuid
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                    ('Data4', ctypes.c_ubyte * 8)]

    def guid(text):
        return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

    def method(pointer, index, *argtypes):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])
        return lambda *args: function(pointer, *args)

    # S_OK or S_FALSE (already initialised on this thread, as Qt's GUI thread is): balance it.
    # RPC_E_CHANGED_MODE (another apartment) is fine to use as it is and must not be balanced.
    initialised = ole32.CoInitializeEx(None, 2) in (0, 1)  # COINIT_APARTMENTTHREADED
    enumerator = ctypes.c_void_p()
    try:
        clsid, iid = guid('BCDE0395-E52F-467C-8E3D-C4579291692E'), guid('A95664D2-9614-4F35-A746-DE8DB63617E6')
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 23, ctypes.byref(iid), ctypes.byref(enumerator)) or not enumerator.value:
            return None
        device = ctypes.c_void_p()
        try:
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender = 0, eConsole = 0, &device)
            if method(enumerator, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(0, 0, ctypes.byref(device)) or not device.value:
                return None  # E_NOTFOUND: no output device.
            identifier = ctypes.c_void_p()
            try:
                if method(device, 5, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(identifier)) or not identifier.value:  # IMMDevice::GetId
                    return None
                return ctypes.wstring_at(identifier.value)
            finally:
                if identifier.value: ole32.CoTaskMemFree(identifier)
                method(device, 2)()  # Release
        finally:
            method(enumerator, 2)()  # Release
    finally:
        if initialised: ole32.CoUninitialize()
