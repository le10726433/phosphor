"""Cross-platform GPU Compute BVH Path Tracer for Phosphor (macOS Metal & Windows DX12/Vulkan via wgpu)."""

try:
    import wgpu  # noqa: F401
    WGPU_AVAILABLE = True
except ImportError:
    WGPU_AVAILABLE = False
