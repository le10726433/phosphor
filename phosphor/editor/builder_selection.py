"""Axis constraints and path/rectangle selection, independent of camera zoom."""
import numpy as np

def dashed_lines(points,dash=6.,gap=4.):
    result=[];offset=0.;period=dash+gap
    for a,b in zip(points,points[1:]):
        delta=b-a;length=float(np.linalg.norm(delta));cursor=0.
        while cursor<length-1e-9:
            phase=offset%period;step=min(length-cursor,(dash-phase) if phase<dash else period-phase)
            if phase<dash and step>1e-9:result.append((a+delta*(cursor/max(length,1e-12)),a+delta*((cursor+step)/max(length,1e-12))))
            cursor+=max(step,1e-9);offset=(offset+max(step,1e-9))%period
    return result

def axis_delta(delta):
    result=np.asarray(delta,dtype=float).copy()
    result[1 if abs(result[0])>=abs(result[1]) else 0]=0.
    return result

def intersects_box(path,a,b):
    low=np.minimum(a,b);high=np.maximum(a,b)
    for start,end in zip(path,path[1:]):
        first,last=0.,1.;delta=end-start
        for axis in (0,1):
            if abs(delta[axis])<1e-12:
                if start[axis]<low[axis] or start[axis]>high[axis]:first,last=1.,0.;break
            else:
                t=sorted(((low[axis]-start[axis])/delta[axis],(high[axis]-start[axis])/delta[axis]))
                first=max(first,t[0]);last=min(last,t[1])
        if first<=last:return True
    return False
