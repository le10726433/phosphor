"""Display-only cubic interpolation; never used for capture or audio output."""
import numpy as np

INTERPOLATION_FACTORS = {'Off':1, 'Cubic 4x':4, 'Cubic 8x':8}


def interpolate_trace(points, mode):
    factor = INTERPOLATION_FACTORS[mode]
    if factor == 1 or len(points) < 2:
        return points
    points = np.asarray(points, dtype=np.float32)
    p1, p2 = points[:-1], points[1:]
    p0 = np.concatenate((points[:1],points[:-2]))
    p3 = np.concatenate((points[2:],points[-1:]))
    t = (np.arange(factor,dtype=np.float32)/factor)[None,:,None]
    # Catmull–Rom through each sample, without closing the ends of a live trace.
    a = 2*p1
    b = p2-p0
    c = 2*p0-5*p1+4*p2-p3
    d = -p0+3*p1-3*p2+p3
    segments = .5*(a[:,None,:]+t*(b[:,None,:]+t*(c[:,None,:]+t*d[:,None,:])))
    result = np.concatenate((segments.reshape(-1,2),points[-1:]))
    result[::factor] = points  # Preserve every original sample exactly.
    return result
